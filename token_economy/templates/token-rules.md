<!-- tke:token-rules -->
## Token discipline
- Search before reading: grep/glob first, then read line ranges, not whole files.
- Change files with targeted edits; never rewrite a whole file to change a few lines.
- Don't repeat file contents, diffs or command output back; summarise.
- Hand wide searches or many-file reading to a subagent (Explore) that returns a short report.
- Keep command output small: pipe through grep/head/tail; run tests with quiet flags, one test file first.
- When a task is done, say so in one line and suggest starting a fresh session.
<!-- /tke:token-rules -->
