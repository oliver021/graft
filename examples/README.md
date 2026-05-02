# Graft Examples

Ready-to-run showcase queries and demo recording guide.

---

## Saved Queries (`.gql` files)

Run any of these with:
```bash
graft query --file examples/dead_code.gql --db sqlite:///myproject.db
```

### `dead_code.gql`
Finds functions defined but never called anywhere in the indexed codebase.
Candidates for removal or entry points called from outside the indexed scope.

### `risky_calls.gql`
Identifies every call to `eval`, `exec`, `open`, `subprocess`, and `__import__`.
Shows the enclosing function, call site, and filename — no source reading required.

### `complexity_hotspots.gql`
Functions with deep call chains (depth > 4).
Deep chains are where bugs hide and refactors break things.

### `imports_audit.gql`
Full import map: every import statement across the entire indexed codebase.
Pipe to CSV (`--format csv`) for a complete dependency inventory.

---

## Recording the Demo GIF

### Step 1: Install demo tools
```bash
# From the graft-ql repo root:
pip install -e '.[demo]'
```

This installs `asciinema` and `agg` as optional dev dependencies.

### Step 2: Run the recording script
```bash
bash examples/record_demo.sh
```

The script will:
- Create a temp directory
- Start asciinema recording
- Guide you through the demo

### Step 3: Type the commands
Open `examples/DEMO_COMMANDS.txt` in a second window.
Copy-paste each command into the recording terminal, one at a time.
Pause ~1-2 seconds between commands.

### Step 4: Finish and convert
When done, press `Ctrl+D` to stop recording.
The script will auto-convert the recording to `docs/demo.gif`.

### Step 5: Uncomment the README
Open `README.md` and uncomment the GIF line:
```markdown
![Graft demo](docs/demo.gif)
```

---

## Using `showcase.md`

`showcase.md` is a narrated walkthrough for streams and talks.
Read it while demoing — it has talking points and Q&A prep for each query.

```bash
# Index a known codebase (e.g. Flask)
graft index ./flask-src/src --db flask.db

# Then follow showcase.md for each query and the commentary to give
```

---

## Quick Start for Demos

```bash
# 1. Index any Python codebase
graft index ./src --db my.db

# 2. See what you can query
graft entities

# 3. See what fields are available
graft fields function
graft fields function.calls

# 4. Run a saved query
graft query --file examples/dead_code.gql --db my.db

# 5. Or write your own
graft query 'from function as fn select fn.name, fn.filename' --db my.db
```

---

## Files in This Directory

- `dead_code.gql` — query file
- `risky_calls.gql` — query file
- `complexity_hotspots.gql` — query file
- `imports_audit.gql` — query file
- `showcase.md` — narrated demo walkthrough with talking points
- `record_demo.sh` — script to record an interactive GIF
- `DEMO_COMMANDS.txt` — exact commands to type during recording
- `README.md` — this file
