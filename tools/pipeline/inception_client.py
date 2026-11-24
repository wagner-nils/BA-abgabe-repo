#!/usr/bin/env python3
"""A Converse-shaped client for the Inception chat-completions API (pre-analysis specification §4g, 2026-10-30).

The diffusion language model Mercury 3 is served by Inception's OpenAI-compatible API, not by Amazon Bedrock. So
that it runs through exactly the machinery every other model ran through -- the certified gate, the journalled
runner with retry policy v2, the ONE response validator, the evaluator -- this module translates one Bedrock
Converse request into one chat-completions request and the answer back into a Converse response. Nothing else
changes: the system prompt, the tool schema and the user turn are the pinned instrument's, byte for byte.

Request: `system` text blocks -> one `system` message (no cache point is ever sent for these models); the user
turn's text -> one `user` message; `toolConfig.tools[].toolSpec` -> `tools[]` (`type: function`, `parameters` =
the pinned JSON Schema, `strict` not set, as in the registered request); `toolChoice {"tool": {"name"}}` ->
the named `tool_choice` object (`{"any"}` -> "required", `{"auto"}` -> "auto"); `inferenceConfig.maxTokens` ->
`max_completion_tokens`, `temperature` -> `temperature`; `additionalModelRequestFields` (here: `reasoning_effort`)
-> merged into the body.

Response: every `tool_calls[]` entry -> a `toolUse` block (`input` = the parsed JSON object; arguments that are not
a JSON object are passed through as the raw string, so the validator rejects them as schema-invalid instead of
this adapter repairing anything); non-empty `content` -> a `text` block before them. `finish_reason`: `length` ->
`max_tokens`; `stop` or `tool_calls` with at least one tool call -> `tool_use` (Mercury reports `stop` beside a
forced tool call), without one -> `end_turn`; `content_filter` -> `content_filtered`; a missing or unknown finish
reason -> no stopReason (refused downstream). A malformed envelope becomes a `providerMalformed` block (a response
failure with its usage kept), never an infrastructure error. Usage: `prompt_tokens` ->
`inputTokens`, `completion_tokens` (reasoning included) -> `outputTokens`, `total_tokens` -> `totalTokens`; the
provider's automatic prompt caching (`cached_tokens`) and `reasoning_tokens` go to `additionalModelResponseFields`
and never into the Converse cache counters, because no cache point was sent and the gate's check 5n asserts none.

HTTP errors become botocore `ClientError`s with the codes the runner already classifies: 429 ->
ThrottlingException, 500/502/503/504 -> ServiceUnavailableException (an availability failure whose billing is
uncertain), 400 -> ValidationException, 401/403 -> AccessDeniedException, 402 -> ServiceQuotaExceededException,
404 -> ResourceNotFoundException; any other status -> ProviderHTTPError (billing uncertain). A transport failure
(timeout, connection) is raised unchanged.

The key is read from the environment (`INCEPTION_API_KEY`, loaded from `.env` by the runner); it is never written
anywhere.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.inceptionlabs.ai/v1/chat/completions"
KEY_VAR = "INCEPTION_API_KEY"
STATUS_CODE = {429: "ThrottlingException", 500: "ServiceUnavailableException", 502: "ServiceUnavailableException",
               503: "ServiceUnavailableException", 504: "ServiceUnavailableException", 400: "ValidationException",
               401: "AccessDeniedException", 403: "AccessDeniedException", 402: "ServiceQuotaExceededException",
               404: "ResourceNotFoundException"}
FINISH = {"length": "max_tokens", "content_filter": "content_filtered"}


class AdapterError(ValueError):
    """A Converse request this adapter cannot express faithfully; raised before anything is sent."""


def _text_of(blocks) -> str:
    if isinstance(blocks, str):
        return blocks
    out = []
    for b in blocks or []:
        if not isinstance(b, dict):
            raise AdapterError(f"content block is not an object: {type(b).__name__}")
        if "text" in b:
            out.append(b["text"])
        elif "cachePoint" in b:
            raise AdapterError("a cache point cannot be sent to this provider (no cache point for §4g models)")
        else:
            raise AdapterError(f"unsupported content block {sorted(b)}")
    return "".join(out)


def to_chat_body(kw: dict) -> dict:
    """One Converse request (the keyword arguments of `converse()`) -> one chat-completions body."""
    known = {"modelId", "system", "messages", "toolConfig", "inferenceConfig", "additionalModelRequestFields"}
    extra = set(kw) - known
    if extra:
        raise AdapterError(f"unsupported Converse keys {sorted(extra)}")
    msgs = []
    if kw.get("system"):
        msgs.append({"role": "system", "content": _text_of(kw["system"])})
    for m in kw.get("messages") or []:
        if m.get("role") not in ("user", "assistant"):
            raise AdapterError(f"unsupported role {m.get('role')!r}")
        msgs.append({"role": m["role"], "content": _text_of(m.get("content"))})
    body = {"model": kw["modelId"], "messages": msgs}
    tc = kw.get("toolConfig")
    if tc:
        body["tools"] = [{"type": "function", "function": {"name": t["toolSpec"]["name"],
                                                             "description": t["toolSpec"].get("description", ""),
                                                             "parameters": t["toolSpec"]["inputSchema"]["json"]}}
                         for t in tc.get("tools") or []]
        choice = tc.get("toolChoice")
        if isinstance(choice, dict) and isinstance(choice.get("tool"), dict):
            body["tool_choice"] = {"type": "function", "function": {"name": choice["tool"]["name"]}}
        elif isinstance(choice, dict) and "any" in choice:
            body["tool_choice"] = "required"
        elif isinstance(choice, dict) and "auto" in choice:
            body["tool_choice"] = "auto"
        elif choice is not None:
            raise AdapterError(f"unsupported toolChoice {choice!r}")
    inf = kw.get("inferenceConfig") or {}
    unknown_inf = set(inf) - {"maxTokens", "temperature"}
    if unknown_inf:
        raise AdapterError(f"unsupported inferenceConfig keys {sorted(unknown_inf)}")
    if "maxTokens" in inf:
        body["max_completion_tokens"] = inf["maxTokens"]
    if "temperature" in inf:
        body["temperature"] = inf["temperature"]
    for k, v in (kw.get("additionalModelRequestFields") or {}).items():
        if k in body:
            raise AdapterError(f"additional field {k!r} would overwrite a translated field")
        body[k] = v
    return body


def to_converse_response(js: dict, latency_ms: int | None = None) -> dict:
    """One chat-completions response -> one Converse response (see the module docstring for the mapping).

    Codex §4g round 1 (#2, #3): nothing malformed is repaired or turned into an infrastructure error. An envelope
    that is not exactly one choice with an object message, string-or-null content and well-formed tool calls
    becomes a response whose content is a single `providerMalformed` block (no toolUse, so the runner classifies it
    as a response failure, `no_tool_use`, with its usage kept); a missing or unknown `finish_reason` yields no
    stopReason (the runner then refuses the payload as `unexpected_stop_reason`, as it refuses a Bedrock response
    without one). The provider's original response travels along privately (`providerResponse`, raw journal only).
    Codex §4g round 2 (#1): a body that is not a JSON object (null, a list, unparseable text) and a finish reason that
    is not a string or null are malformed envelopes too, never a Python exception that the runner would count as an
    infrastructure failure."""
    raw = js
    if not isinstance(js, dict):
        js = {}
    u = js.get("usage") if isinstance(js.get("usage"), dict) else {}
    usage = {k_out: u[k_in] for k_in, k_out in (("prompt_tokens", "inputTokens"), ("completion_tokens", "outputTokens"),
                                                ("total_tokens", "totalTokens")) if k_in in u}
    ptd = u.get("prompt_tokens_details") if isinstance(u.get("prompt_tokens_details"), dict) else {}
    ctd = u.get("completion_tokens_details") if isinstance(u.get("completion_tokens_details"), dict) else {}
    choices = js.get("choices")
    fr = choices[0].get("finish_reason") if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) else None
    fr_ok = fr is None or isinstance(fr, str)
    details = {"provider": "inception", "model": js.get("model"), "finish_reason": fr if fr_ok else repr(fr), "warning": js.get("warning"),
               "cached_tokens": ptd.get("cached_tokens", u.get("cached_input_tokens")),
               "reasoning_tokens": ctd.get("reasoning_tokens", u.get("reasoning_tokens"))}

    def malformed(why: str) -> dict:
        out = {"output": {"message": {"role": "assistant", "content": [{"providerMalformed": why}]}}, "usage": usage,
               "additionalModelResponseFields": dict(details, malformed=why), "providerResponse": raw}
        if latency_ms is not None:
            out["metrics"] = {"latencyMs": latency_ms}
        return out

    if not isinstance(raw, dict):
        return malformed(f"response body is {type(raw).__name__}, not a JSON object")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return malformed(f"choices is not exactly one object ({type(choices).__name__}, "
                         f"{len(choices) if isinstance(choices, list) else 'n/a'})")
    msg = choices[0].get("message")
    if not isinstance(msg, dict):
        return malformed(f"message is {type(msg).__name__}, not an object")
    if msg.get("content") is not None and not isinstance(msg.get("content"), str):
        return malformed(f"message.content is {type(msg.get('content')).__name__}, not a string or null")
    if not fr_ok:
        return malformed(f"finish_reason is {type(fr).__name__}, not a string or null")
    calls = msg.get("tool_calls")
    if calls is not None and not (isinstance(calls, list) and all(isinstance(c, dict) and isinstance(c.get("function"), dict)
                                                                  for c in calls)):
        return malformed("tool_calls is not a list of objects with a function object")
    content = []
    if isinstance(msg.get("content"), str) and msg["content"].strip():
        content.append({"text": msg["content"]})
    for c in calls or []:
        fn = c["function"]
        args_raw = fn.get("arguments")       # never `raw`: that name holds the provider's original response
        try:
            parsed = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
        except ValueError:
            parsed = args_raw
        content.append({"toolUse": {"toolUseId": c.get("id"), "name": fn.get("name"),
                                    "input": parsed if isinstance(parsed, dict) else args_raw}})
    out = {"output": {"message": {"role": "assistant", "content": content}}, "usage": usage,
           "additionalModelResponseFields": details, "providerResponse": raw}
    if fr in FINISH:
        out["stopReason"] = FINISH[fr]
    elif fr in ("stop", "tool_calls"):
        out["stopReason"] = "tool_use" if calls else "end_turn"
    # any other finish reason, None included: no stopReason (refused downstream, never repaired here)
    if latency_ms is not None:
        out["metrics"] = {"latencyMs": latency_ms}
    return out


def to_client_error(status: int, js: dict):
    from botocore.exceptions import ClientError           # declared dependency of the pipeline
    err = js.get("error") if isinstance(js, dict) else None
    err = err if isinstance(err, dict) else {}
    # Codex §4g round 1 (#4): only the documented pre-serving rejections map to codes the runner treats as unbilled;
    # any other status keeps an uncertain billing state (a code outside KNOWN_UNBILLED_MARKERS)
    code = STATUS_CODE.get(status, "ServiceUnavailableException" if status >= 500 else "ProviderHTTPError")
    return ClientError({"Error": {"Code": code, "Message": f"HTTP {status} {err.get('type')}: {err.get('message')}"},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, "Converse")


def urllib_transport(url: str, body: dict, headers: dict, timeout: float) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, body = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read()
    try:
        return status, json.loads(body or b"{}")
    except ValueError:                # an unparseable body reaches the adapter as text (a malformed envelope)
        return status, body.decode("utf-8", "replace")


class InceptionConverseClient:
    """`converse(**kw)` with the Converse signature, served by the Inception API."""

    def __init__(self, endpoint: str = ENDPOINT, api_key: str | None = None, transport=None, timeout: float = 180.0):
        self.endpoint = endpoint
        self._key = api_key if api_key is not None else os.environ.get(KEY_VAR, "")
        if not self._key:
            raise AdapterError(f"{KEY_VAR} is not set (the runner loads it from .env)")
        self.transport = transport or urllib_transport
        self.timeout = timeout

    def converse(self, **kw) -> dict:
        body = to_chat_body(kw)
        headers = {"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}
        t0 = time.time()
        status, js = self.transport(self.endpoint, body, headers, self.timeout)
        if status != 200:
            raise to_client_error(status, js)
        return to_converse_response(js, latency_ms=int(1000 * (time.time() - t0)))
