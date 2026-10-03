# Working Agreement

Agreements that apply to all contributions to this repository.

1. **Never commit or document sensitive information.**
   No API keys, secrets, private keys, wallet material, tokens, passwords, or personal data in
   code, tests, fixtures, docs, commit messages, or logs. Adapter credentials stay in environment
   variables. Read credentials from the environment only, and never add an environment variable
   to a test in a way that makes the test depend on it.

2. **Always commit and push code after a code change.**
   A code change is not finished when the file is written; it is finished when the change is
   committed and pushed. Follow the commit-message rules in AGENTS.md: no Conventional Commits
   syntax, and no issue or pull request number in the subject.

3. **Fix defects on the spot, unless the owner decides otherwise.**
   When a defect is found in the code being worked on, fix it as part of the same change rather
   than reporting it and moving on. Defer only when the owner explicitly decides to defer, and
   record the deferral.

4. **Never make code changes unless they are defects.**
   Keep the change surface limited to defect fixes. Do not add unrelated features, refactors,
   renames, abstractions, drive-by cleanups, or speculative improvements. Note unrelated
   observations instead of changing them.

5. **Always keep corresponding documentation in sync with code changes.**
   A code change updates the documents and generated artifacts that describe it, in the same
   change. This includes concept and integration docs, coding-standards guidance, and the
   generated stubs and docstrings. Maintainer-owned files such as `RELEASES.md` are updated by
   maintainers, not by agents.
