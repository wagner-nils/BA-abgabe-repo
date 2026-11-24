#!/usr/bin/env python3
"""Tests of the DSL estimator's pure functions (tools/pipeline/dsl.py): identities that hold for the estimator,
the resampling, and the indicator construction. No model call, no repository file."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dsl                                                          # noqa: E402
import evaluate as ev                                               # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append((name, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not cond else ""))


def main() -> None:
    # a population of 10 units, 4 sampled with pi = 0.4 (one stratum): HT of the sample reproduces the sampled mean / pi
    units = [{"yhat": yh, "R": R, "pi": 0.4 if R else None, "y": y if R else None, "art": a} for yh, R, y, a in
             [(1, 1, 1, "A"), (0, 1, 1, "A"), (1, 1, 0, "B"), (0, 1, 0, "B"), (1, 0, None, "C"), (1, 0, None, "C"),
              (0, 0, None, "D"), (0, 0, None, "D"), (1, 0, None, "E"), (0, 0, None, "E")]]
    pt = dsl.dsl_point(units)
    check("D1 naive = mean of yhat over the population", abs(pt["naive"] - 0.5) < 1e-12, str(pt))
    check("D2 Horvitz-Thompson reference = sum(y/pi)/N", abs(pt["reference_ht"] - (2 / 0.4) / 10) < 1e-12, str(pt))
    # DSL = naive + (1/N) sum_R (y - yhat)/pi: residuals (1-1)=0, (1-0)=+1, (0-1)=-1, (0-0)=0 -> 0
    check("D3 DSL = naive + the design-weighted mean residual on the sample", abs(pt["dsl"] - 0.5) < 1e-12, str(pt))
    perfect = [dict(u, y=u["yhat"] if u["R"] else None) for u in units]
    check("D4 a model that agrees with the reference on every sampled unit yields DSL = naive", abs(dsl.dsl_point(perfect)["dsl"] - 0.5) < 1e-12)
    allR = [dict(u, R=1, pi=1.0, y=1 if i % 2 == 0 else 0) for i, u in enumerate(units)]
    check("D5 with every unit sampled at pi = 1 the DSL estimate is the reference mean", abs(dsl.dsl_point(allR)["dsl"] - 0.5) < 1e-12)
    try:
        dsl.dsl_point([dict(units[0], pi=None)]); bad = False
    except dsl.DslError:
        bad = True
    check("D6 a sampled unit without an inclusion probability is refused", bad)
    bs = dsl.dsl_bootstrap(units, 200, 1); bs2 = dsl.dsl_bootstrap(units, 200, 1)
    check("D7 the article resampling is seeded, ordered and brackets the point estimate", bs == bs2 and bs["lo"] <= pt["dsl"] <= bs["hi"] and bs["articles"] == 5, str(bs))
    one_art = [dict(u, art="Z") for u in units]
    b1 = dsl.dsl_bootstrap(one_art, 50, 3)
    check("D8 a single article gives a degenerate interval at the point estimate", abs(b1["lo"] - pt["dsl"]) < 1e-12 and abs(b1["hi"] - pt["dsl"]) < 1e-12)
    frame_of = {"F1a": "costs", "F2a": "jobs"}
    rows = [{"paragraph_id": "p", "technology": "wind", "F1a": "1", "F2a": "0", "eval_costs": "negative", "comparison_target": "none"},
            {"paragraph_id": "p", "technology": "solar", "F1a": "0", "F2a": "1", "eval_jobs": "positive", "comparison_target": "none"}]
    ind = dsl.indicators(rows, frame_of)
    check("D9 indicators are paragraph-level: technologies, elements and frame=value pairs over every relation",
          ind == {"technology:wind", "technology:solar", "element:F1a", "element:F2a", "eval:costs=negative", "eval:jobs=positive"}, str(ind))
    contract = {"model_id": "m", "region": "r", "prompt_sha256": "p", "tool_config_sha256": "t", "codebook_sha256": "c",
                "inference_config": {"temperature": 0, "maxTokens": 1024}, "additional_model_request_fields": None, "max_tokens": 1024,
                "retry_policy": {"parse": 1, "throttle": 5, "error": 3}, "retry_policy_version": "2", "instrument_version": "1.0", "instrument_v11_sha256": None}
    ra = {"run_id": "a2_sonnet_20261013", "model_id": "m", "region": "r", "tool_config_sha256": "t", "prompt": {"sha256": "p"}, "codebook": {"sha256": "c"},
          "inference_config": {"temperature": 0, "maxTokens": 1024}, "execution_contract": json.loads(json.dumps(contract))}
    def variant(**over):                      # a genuinely different run: contract AND display fields change together
        r = json.loads(json.dumps(ra)); r["execution_contract"].update(over)
        for k, v in over.items():
            if k == "prompt_sha256": r["prompt"]["sha256"] = v
            elif k in ("inference_config", "model_id", "region", "tool_config_sha256"): r[k] = v
        return r
    diffs = [dsl.same_configuration(ra, variant(inference_config={"temperature": 0.5, "maxTokens": 1024})),
             dsl.same_configuration(ra, variant(retry_policy={"parse": 2, "throttle": 5, "error": 3})),
             dsl.same_configuration(ra, variant(additional_model_request_fields={"thinking": {"type": "enabled"}})),
             dsl.same_configuration(ra, variant(prompt_sha256="q", retry_policy_version="3"))]
    edited = json.loads(json.dumps(ra)); edited["model_id"] = "other"
    try:
        dsl.configuration(edited); ed = False
    except dsl.DslError:
        ed = True
    check("D10 configuration equivalence reads the fingerprint-bound contract (decoding, retry allowances, additional model settings, prompt, policy) and refuses a record whose display fields disagree with its contract",
          dsl.same_configuration(ra, ra) == [] and diffs == [["inference_config"], ["retry_policy"], ["additional_model_request_fields"], ["prompt_sha256", "retry_policy_version"]] and ed, str(diffs))
    other = json.loads(json.dumps(ra)); other["run_id"] = "a2_sonnet_20261101"
    try:
        dsl.require_registered(other); reg = False
    except dsl.DslError:
        reg = True
    try:
        dsl.require_registered(ra); reg_ok = True
    except dsl.DslError:
        reg_ok = False
    check("D12 only the registered run supplies the sampled predictions; another otherwise valid A2 run is refused", reg and reg_ok)
    frame = {"paragraphs": [{"paragraph_id": f"P{i}", "article_id": f"A{i % 3}", "digest": f"d{i}", "text_sha256": f"t{i}"} for i in range(6)]}
    def run(ids, run_id, status):
        rec = json.loads(json.dumps(ra)); rec.update({"run_id": run_id, "analysis_status": status})
        return {"rec": rec, "paras": [dict(p, pi=0.5, stratum="de|X", lang="de", N_h=4, n_h=2) for p in frame["paragraphs"] if p["paragraph_id"] in ids],
                "pred": {}, "outcome": {p: "off_topic" for p in ids}}
    a2 = run({"P0", "P1"}, "a2", "registered primary"); comp = run({"P2", "P3"}, "comp", "exploratory frame completion"); a3 = run({"P4", "P5"}, "a3", "fresh-sample evaluation")
    gold1 = [{"paragraph_id": "P0", "tech_wind": "0"}, {"paragraph_id": "P1", "tech_wind": "0"}]
    try:
        dsl.estimate(a2, comp, None, gold1, [], frame, {}, 5, 10, 1, {}); nores = False
    except dsl.DslError:
        nores = True
    try:
        res = dsl.estimate(a2, comp, a3, gold1, [], frame, {}, 5, 10, 1, {}); full = res["population"] == "frame" and res["N"] == 6
    except dsl.DslError as e_:
        full = False; print("   ", e_)
    comp_bad = run({"P2"}, "comp", "exploratory frame completion")
    try:
        dsl.estimate(a2, comp_bad, a3, gold1, [], frame, {}, 5, 10, 1, {}); gap = False
    except dsl.DslError:
        gap = True
    comp_cfg = run({"P2", "P3"}, "comp", "exploratory frame completion"); comp_cfg["rec"]["execution_contract"]["model_id"] = "other"; comp_cfg["rec"]["model_id"] = "other"
    try:
        dsl.estimate(a2, comp_cfg, a3, gold1, [], frame, {}, 5, 10, 1, {}); cfg = False
    except dsl.DslError:
        cfg = True
    check("D11 no estimate without the A3 run (the remainder of the frame is sample-dependent); the frame estimate needs complete, non-overlapping coverage under the registered request",
          nores and full and gap and cfg, f"{nores} {full} {gap} {cfg}")

    # --- design-based standard error (added 2026-10-19 after the PPI ingest, P100)
    # one stratum, N = 10, n = 4, pi = 0.4: Var = (1 - f) s^2 / n with s^2 the sample variance of the residual
    strat = [dict(u, stratum="de|S", lang="de", N_h=10, n_h=4) if u["R"] else u for u in units]
    for u in strat:
        if u["R"]:
            u["resid"] = u["y"] - u["yhat"]
    grp = dsl.collapse_strata([{"stratum": "de|S", "lang": "de", "N_h": 10, "n_h": 4}])
    dv = dsl.design_variance(strat, grp, "resid")
    resid = [u["resid"] for u in strat if u["R"]]                     # 0, +1, -1, 0 -> mean 0, s^2 = 2/3
    s2 = sum((r - sum(resid) / 4) ** 2 for r in resid) / 3
    want = ((1 - 0.4) * s2 / 4) ** 0.5
    check("D13 one stratum: the design-based SE is sqrt((1 - f) s^2 / n) of the residual", abs(dv["se"] - want) < 1e-12 and dv["groups"] == 1 and dv["collapsed"] == [], f"{dv} {want}")
    perf = [dict(u, resid=0.0) if u["R"] else u for u in strat]
    census = [dict(u, R=1, pi=1.0, stratum="de|S", N_h=10, n_h=10, resid=(i % 2)) for i, u in enumerate(strat)]
    grp10 = dsl.collapse_strata([{"stratum": "de|S", "lang": "de", "N_h": 10, "n_h": 10}])
    check("D14 a perfect predictor and a census both give a zero design-based SE",
          dsl.design_variance(perf, grp, "resid")["se"] == 0.0 and dsl.design_variance(census, grp10, "resid")["se"] == 0.0)
    st = [{"stratum": "de|A", "lang": "de", "N_h": 4, "n_h": 1}, {"stratum": "de|B", "lang": "de", "N_h": 20, "n_h": 5},
          {"stratum": "de|C", "lang": "de", "N_h": 5, "n_h": 1}, {"stratum": "de|D", "lang": "de", "N_h": 4, "n_h": 1},
          {"stratum": "en|E", "lang": "en", "N_h": 30, "n_h": 3}]
    g = dsl.collapse_strata(st)
    trailing = dsl.collapse_strata([{"stratum": "de|A", "lang": "de", "N_h": 20, "n_h": 5}, {"stratum": "de|B", "lang": "de", "N_h": 4, "n_h": 1}])
    try:
        dsl.collapse_strata([{"stratum": "fr|A", "lang": "fr", "N_h": 4, "n_h": 1}]); lone = False
    except dsl.DslError:
        lone = True
    check("D15 single-draw strata collapse with the following stratum of their language, a trailing singleton with the preceding group, and a language with one draw is refused",
          g == {"de|A": "de|A+de|B", "de|B": "de|A+de|B", "de|C": "de|C+de|D", "de|D": "de|C+de|D", "en|E": "en|E"} and trailing == {"de|A": "de|A+de|B", "de|B": "de|A+de|B"} and lone, str(g))
    # a pooled group uses the with-replacement form on w_i v_i WITHOUT a finite-population factor (the registered
    # Berliner Morgenpost + DER SPIEGEL weights): A (N 4, n 1, w 4) + B (N 18, n 5, w 3.6) -> n_g 6, N_g 22
    coll = [{"R": 1, "pi": 0.25, "stratum": "de|A", "N_h": 4, "n_h": 1, "v": 1.0}] + \
           [{"R": 1, "pi": 5 / 18, "stratum": "de|B", "N_h": 18, "n_h": 5, "v": float(v)} for v in (0, 1, 0, 0, 1)] + [{"R": 0}] * 16
    wv = [4.0 * 1.0] + [3.6 * v for v in (0, 1, 0, 0, 1)]; m = sum(wv) / 6
    want_c = ((6 / 5) * sum((x - m) ** 2 for x in wv)) ** 0.5 / 22
    dvc = dsl.design_variance(coll, {"de|A": "de|A+de|B", "de|B": "de|A+de|B"}, "v")
    try:
        dsl.design_variance(coll[:-1] + [{"R": 1, "pi": 5 / 18, "stratum": "de|B", "N_h": 18, "n_h": 5, "v": 0.0}], {"de|A": "de|A+de|B", "de|B": "de|A+de|B"}, "v"); bad_n = False
    except dsl.DslError:
        bad_n = True
    check("D16 a pooled group is scored with the with-replacement form and no finite-population factor; a sampled count that disagrees with the allocation is refused",
          abs(dvc["se"] - want_c) < 1e-12 and dvc["collapsed"] == ["de|A+de|B"] and bad_n, f"{dvc} {want_c}")
    res2 = dsl.estimate(a2, comp, a3, gold1, [], frame, {}, 5, 10, 1, {})
    ok_cols = all(k in r for r in res2["classes"].values() for k in ("se_design_pp", "ci_design_lo_pp", "ci_design_hi_pp", "se_reference_design_pp"))
    sym = all(abs((r["ci_design_hi_pp"] - r["dsl_pp"]) - (r["dsl_pp"] - r["ci_design_lo_pp"])) < 1e-9 for r in res2["classes"].values())
    check("D17 the frame estimate carries the design-based SE, a symmetric normal interval and the reference-only SE, and reports its variance groups",
          ok_cols and sym and res2["variance_groups"] == {"n": 1, "strata": 1, "collapsed": []}, str(res2.get("variance_groups")))
    # D18: two uncollapsed strata with unequal weights, hand-computed: A (N 10, n 4, residuals 1,0,0,-1), B (N 6, n 2, residuals 1,0)
    # Var = 100*(1-0.4)*(2/3)/4 + 36*(1-1/3)*0.5/2 = 10 + 6 = 16 over N^2 = 256 -> SE 0.25; and the total is the sum over groups
    two = [{"R": 1, "pi": 0.4, "stratum": "de|A", "N_h": 10, "n_h": 4, "v": float(v)} for v in (1, 0, 0, -1)] + \
          [{"R": 1, "pi": 2 / 6, "stratum": "en|B", "N_h": 6, "n_h": 2, "v": float(v)} for v in (1, 0)] + [{"R": 0}] * 10
    g2 = dsl.collapse_strata([{"stratum": "de|A", "lang": "de", "N_h": 10, "n_h": 4}, {"stratum": "en|B", "lang": "en", "N_h": 6, "n_h": 2}])
    se_two = dsl.design_variance(two, g2, "v")["se"]
    onlyA = [u if u.get("stratum") == "de|A" else {"R": 0} for u in two]; onlyB = [u if u.get("stratum") == "en|B" else {"R": 0} for u in two]
    seA = dsl.design_variance(onlyA, g2, "v")["se"]; seB = dsl.design_variance(onlyB, g2, "v")["se"]
    check("D18 two strata with unequal weights give SE 0.25 by hand, and the variance is the sum over groups",
          abs(se_two - 0.25) < 1e-12 and abs(seA ** 2 - 10 / 256) < 1e-12 and abs(seB ** 2 - 6 / 256) < 1e-12 and abs(se_two ** 2 - seA ** 2 - seB ** 2) < 1e-12, f"{se_two} {seA} {seB}")
    # D19: exact enumeration of every sample. Uncollapsed: the estimator is unbiased for the design variance of the HT
    # total; pooled (a single-draw stratum): its expectation is at least the design variance (conservative).
    from itertools import combinations
    def enumerate_design(popA, nA, popB, nB, groups):
        NA, NB = len(popA), len(popB); N = NA + NB
        tot_true, est, samples = [], [], 0
        for sa in combinations(range(NA), nA):
            for sb in combinations(range(NB), nB):
                us = [{"R": 1, "pi": nA / NA, "stratum": "de|A", "N_h": NA, "n_h": nA, "v": popA[i]} for i in sa] + \
                     [{"R": 1, "pi": nB / NB, "stratum": "de|B", "N_h": NB, "n_h": nB, "v": popB[i]} for i in sb] + [{"R": 0}] * (N - nA - nB)
                tot_true.append(sum(popA[i] / (nA / NA) for i in sa) + sum(popB[i] / (nB / NB) for i in sb))
                est.append(dsl.design_variance(us, groups, "v")["se"] ** 2 * N * N); samples += 1
        mu = sum(tot_true) / samples
        return sum((x - mu) ** 2 for x in tot_true) / samples, sum(est) / samples
    popA = [1.0, -1.0, 0.0, 0.0]; popB = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    true_u, exp_u = enumerate_design(popA, 2, popB, 2, {"de|A": "de|A", "de|B": "de|B"})
    true_c, exp_c = enumerate_design(popA, 1, popB, 2, {"de|A": "de|A+de|B", "de|B": "de|A+de|B"})
    # with the pooled finite-population factor the pooled estimator would be biased low: show it is not used
    frac_c = exp_c * (1 - 3 / 10)
    check("D19 enumerated over every sample: unbiased for uncollapsed strata, conservative (not below the design variance) for a pooled single-draw stratum, and the pooled factor would have been too low",
          abs(true_u - exp_u) < 1e-9 and exp_c >= true_c - 1e-9 and frac_c < true_c, f"uncollapsed {true_u:.4f}/{exp_u:.4f}; pooled {true_c:.4f}/{exp_c:.4f}; with factor {frac_c:.4f}")
    # D20: a non-degenerate estimate: the model marks wind on sampled P0 where the reference has nothing -> residual -1 on P0, 0 on P1;
    # stratum de|X (N 4, n 2, w 2): Var = 16 * 0.5 * s^2 / 2 with s^2 = 0.5 -> 2, over N^2 = 36 -> SE = sqrt(2)/6
    a2w = run({"P0", "P1"}, "a2", "registered primary"); a2w["rec"]["run_id"] = "a2_sonnet_20261013"
    a2w["pred"] = {"P0": [{"paragraph_id": "P0", "technology": "wind", "comparison_target": "none"}]}
    res3 = dsl.estimate(a2w, comp, a3, gold1, [], frame, {}, 1, 10, 1, {})
    rw = res3["classes"]["technology:wind"]
    se_want = (2 ** 0.5) / 6 * 100
    check("D20 a non-degenerate class pins the SE by hand, the 1.96 normal half-width, strictly ordered interval endpoints and the reference-only SE of zero",
          abs(rw["se_design_pp"] - se_want) < 1e-9 and abs((rw["ci_design_hi_pp"] - rw["dsl_pp"]) / rw["se_design_pp"] - 1.959963984540054) < 1e-9
          and rw["ci_design_lo_pp"] < rw["dsl_pp"] < rw["ci_design_hi_pp"] and rw["se_reference_design_pp"] == 0.0, str(rw))

    # D21-D22: Figure 6 (figures.py dsl) from a constructed frame estimate: only reportable elements, the design-based
    # interval (never the article-bootstrap interval), sample-only = reference +- z * reference SE, the double dagger on
    # an element whose adjusted interval is wider, the worked example shaded and labelled
    import figures as fg
    z = dsl.Z975
    def cls(est, se, ref, se_ref, naive, reported=True, support=20):
        return {"dsl_pp": est, "se_design_pp": se, "ci_design_lo_pp": est - z * se, "ci_design_hi_pp": est + z * se,
                "lo_pp": 1.0, "hi_pp": 59.0, "naive_pp": naive, "reference_ht_pp": ref, "se_reference_design_pp": se_ref,
                "gold_support": support, "reported": reported, "registered_displacement_pp": None}
    fres = {"population": "frame", "N": 600, "articles": 60, "sampled": 120, "threshold": 5, "failed_executions": {"r": 4},
            "classes": {"element:F2b": cls(30.0, 2.0, 32.0, 3.0, 20.0), "element:F6b": cls(5.0, 3.0, 6.0, 2.0, 7.0),
                        "element:F2a": cls(2.0, 1.0, 1.0, 1.0, 2.0, reported=False, support=2),
                        "technology:wind": cls(50.0, 1.0, 50.0, 1.0, 50.0)}}
    fig = fg.fig_dsl(fres)
    zz = z * 2.0; zr = z * 3.0
    check("D21 Figure 6 draws the reportable elements only, sorted by the adjusted estimate, with the design-based interval, the sample-only interval reference +- z * SE and the model-only share; the wider adjusted interval is marked and the worked example shaded and labelled",
          "Investment, industry (F2b)" in fig and "Technical performance (F6b)\\,\\ddag" in fig and "(F2b)\\,\\ddag" not in fig
          and "Employment (F2a)" not in fig and "Wind" not in fig
          and fig.index("Investment, industry (F2b)") < fig.index("Technical performance (F6b)")
          and f"(30.000,1.00) -= ({zz:.3f},0) += ({zz:.3f},0)" in fig and f"(32.000,1.27) -= ({zr:.3f},0) += ({zr:.3f},0)" in fig
          and "(20.000,0.73)" in fig and "29.000" not in fig and "\\fill[figGreyC]" in fig and "(worked example)" in fig
          # 2026-11-12, second cut pass (75k): the caption states the failed executions as "(4 paragraphs)" and the
          # frame size once, so both counts are still checked, each in its new place
          and "\\label{fig:dsl}" in fig and "failed executions (4 paragraphs) unmarked" in fig
          and "of 600 eligible-frame paragraphs" in fig, fig[-600:])
    bad = json.loads(json.dumps(fres)); bad["classes"]["element:F2b"]["ci_design_hi_pp"] = 59.0
    try:
        fg.fig_dsl(bad); refused = False
    except SystemExit:
        refused = True
    check("D22 Figure 6 refuses an output whose design interval is not the estimate +- z * SE", refused)
    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        sys.exit(1)


if __name__ == "__main__":
    main()
