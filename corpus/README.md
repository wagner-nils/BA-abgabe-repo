# corpus/

The index of the hand-collected news corpus. The article files themselves are not part of the repository.

- `manifest.csv`: one row per article (columns below).
- `articles/` (not included): one Markdown file per article, `<id>_<outlet>.md`, as named in the manifest's `file`
  column. `tools/annotation/build_sheets.py` reads the body text from these files and checks every paragraph against
  the hash recorded in the split registry.

| Column | Content |
|---|---|
| `id` | `A001`, `A002`, … in collection order |
| `batch` | collection round |
| `outlet`, `country`, `language` | publication, where it is based (ISO 3166-1), language of the article (ISO 639-1) |
| `publication_date`, `retrieved_at` | as stated by the outlet; access date |
| `headline`, `url` | verbatim headline, canonical URL |
| `technologies` | technologies the article covers |
| `selected_for` | the case the article was selected for |
| `file` | path relative to this folder |
