# registry/mosio

Static Mosio registry: `registry.json` lists the items, `<name>.json` holds one item (type `mosio:recipe`).
Each item has `files[]` of `{path, role, content}`: `RECIPE.md` (art direction), `plan.md` (beat plan), and
`example/` and `example-9x16/` (Mosio projects: `index.html` + `mosio.json`, fonts embedded).

## sha256

`sha256` is the lowercase hex SHA-256 over every entry of `files`, in order of `path` sorted by bytes, each
contributing `path` + NUL byte + `content` + NUL byte (UTF-8), concatenated. The `sha256` field itself is not part of it.
An item with a missing or mismatched `sha256` must be rejected.

Licenses and upstream credits are in `NOTICES`.
