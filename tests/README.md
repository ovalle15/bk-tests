# File Counter Buildkite Plugin

A Buildkite plugin that counts the files in the checkout after the command runs and annotates the build with the result.

## Example

Add the following to your `pipeline.yml`:

```yml
steps:
  - command: make build
    plugins:
      - ovalle15/file-counter#v0.1.0:
          pattern: "*.go"
```

## Configuration

### `pattern` (optional, string)

Filename glob to match, such as `*.go` or `*.md`. The `.git` directory is ignored.

Default: `*` (all files)

## Developing

To run the linter:

```bash
docker run -it --rm -v "$PWD:/plugin:ro" buildkite/plugin-linter --id ovalle15/file-counter
```
