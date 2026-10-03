# Contributing

Thanks for considering a contribution. This file is a generic starting
point shipped by the `github-template` template repository; adjust it once
the project created from this template has its own conventions.

## Before you start

- Search open issues and pull requests first, so effort is not duplicated.
- For a change of any size, open an issue describing what you want to do
  before writing code, so the approach can be discussed up front.

## Making a change

1. Fork the repository and create a branch off `main`.
2. Install pre-commit and the hooks this repo wires up:

   ```shell
   pip install pre-commit
   pre-commit install
   ```

3. Make your change, and run the checks locally before opening a pull
   request:

   ```shell
   pre-commit run --all-files
   ```

   `make coverage` runs the tests under coverage.py, node's test runner and
   kcov, each in a podman container, and fails unless the Python (lines and
   branches), the JavaScript and the shell scripts the Makefile lists (lines)
   reach 100%. It needs podman on `PATH`, and it also runs as a pre-push
   hook, so run `pre-commit install` again in an existing clone to pick up
   that stage. A new script ships with tests that reach every line of it,
   and a script without an extension is added to the Makefile's list for
   its language.

4. Commit using [Conventional Commits](https://www.conventionalcommits.org/),
   for example `fix: correct a typo in the README`. No ticket prefix is
   required by default.
5. Open a pull request against `main` using the template in
   [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md). Open
   it as a draft first if the checks take a while to run, and mark it ready
   once they are green.

## Updating the test dependencies

`tests/requirements.in` carries the exact pins. `tests/requirements.txt` is
a lock compiled from it with every hash, which `pip install
--require-hashes` checks. Renovate bumps both. To change one by hand, edit
the `.in` file and regenerate the lock in a container, from `tests/`:

```shell
podman run --rm -v "$PWD:/w:rw,Z" -w /w ghcr.io/astral-sh/uv:python3.14-trixie-slim \
  uv pip compile --generate-hashes --python-version=3.14 --exclude-newer=P7D \
  --output-file=requirements.txt requirements.in
```

That is the command in the lock's own header, which Renovate replays.
`--exclude-newer=P7D` leaves out anything released in the last seven days,
dependencies of dependencies included.

## License

By contributing, you agree that your contributions will be licensed under
this repository's [Apache License 2.0](LICENSE.md). A project created from
this template that changes its license should update this line to match.

## Code of Conduct

Participation in this project is governed by
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Security issues

Do not open a public issue for a security vulnerability. See
[SECURITY.md](SECURITY.md) instead.
