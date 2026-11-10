# Task: plan the build targets

Spec hash: `$spec_hash`. Read every file under `$spec_dir/`. Each file in `$spec_dir/platforms/` describes one
target to generate:

$platform_specs

Return one target per platform spec with:
- `name`: short lowercase identifier (e.g. the language)
- `platform_spec`: the platform spec path above
- `comment`: the language's line-comment token
- `build`: argv to build it from the repository root ([] if none), using `{dir}` for the target directory
- `launch`: argv that starts the program from the repository root, using `{dir}`
- `sources`: globs relative to the target directory matching the source files a developer writes (not build output)
- `selfcheck`: optional fast syntax/type-check argv lists

The target directory will be `$generated_root/<name>`. Only these toolchain commands exist
(argv[0] must be one of them, or a `{dir}/` path):

$toolchain

Take paths, entry points and build commands from the platform specs exactly. Do not invent tools.
