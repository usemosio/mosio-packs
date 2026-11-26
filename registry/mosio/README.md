# registry/mosio

Static Mosio registry: `registry.json` lists the items, `<name>.json` holds one item (types `mosio:recipe`, `mosio:scene`, `mosio:font`).
Each item has `files[]` of `{path, role, content}`: `RECIPE.md` (art direction), `plan.md` (beat plan), and
`example/` (a Mosio project: `index.html` + `mosio.json`, fonts embedded) plus `example-9x16/` or `example-16x9/` for the other ratio.
The four engine recipes also carry `params.json` (the parameter form: JSON Schema plus an example) and `recipe/` (their templates).

## sha256

`sha256` is the lowercase hex SHA-256 over every entry of `files`, in order of `path` sorted by bytes, each
contributing `path` + NUL byte + `content` + NUL byte (UTF-8), concatenated. The `sha256` field itself is not part of it.
An item with a missing or mismatched `sha256` must be rejected.

Licenses and upstream credits are in `NOTICES`.

## Scenes

`mosio:scene` items are single HTML + GSAP scenes (1,041 of them). Files: `SCENE.md` (what it is, when to use it,
variables) and `scene.html` (role `source`: the scene, with its upstream notice in the header comment, imported at add
time). Item fields: `variables`, `demo_values`, `demo_background`, `duration`, `webgl`, `plugins` (GSAP plugin names).
Scene licences and credits: `NOTICES-scenes.md`.
