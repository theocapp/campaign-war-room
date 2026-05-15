# How to Build This — Step by Step Guide

> You don't need to know how to code. Follow these steps exactly.

---

## Starting a Work Session

### 1. Start the app (two terminal tabs)

**Tab 1 — Backend:**
```
cd /Users/theo/campaign-war-room/backend
source .venv/bin/activate
uvicorn app.main:app --reload
```
Leave it running. It should say "Application startup complete."

**Tab 2 — Frontend:**
```
cd /Users/theo/campaign-war-room/frontend
npm run dev
```
Leave it running. It should say "ready in X ms."

Open http://localhost:5173 in your browser.

---

### 2. Start Aider (third terminal tab)

```
cd /Users/theo/campaign-war-room
aider --architect --model ollama/qwen2.5-coder:14b --editor-model ollama/qwen2.5-coder:14b --read PRODUCT_BRIEF.md --read ROADMAP.md
```

Aider will load your project context automatically.

`PRODUCT_BRIEF.md` is the source of truth — what the app is, what's in scope, and what
architectural decisions have already been settled. `ROADMAP.md` tracks what to do next.
Do not re-introduce `REPO_MEMORY.md` or `AGENT_RULES.md`; they described an older
architecture that has been removed.

---

## How to Talk to Aider

You don't write code — you describe what you want in plain English.
Aider figures out which files to change.

**Good prompt structure:**
```
I'm working on [feature/page].
Right now [describe what's happening].
I want it to [describe what you want].
Don't change anything else.
```

**Example:**
```
I'm working on the Review Queue page.
Right now items don't show the source URL.
I want each item to show a clickable link to the original article.
Don't change anything else.
```

---

## When Something Is Broken

Tell Aider exactly what you see:

```
The backend is crashing with this error:
[paste the error here]

I didn't change anything — this just started happening.
```

Aider will read the relevant files and fix it.

---

## After Aider Makes Changes

1. Check the browser — does it look right?
2. Run tests: in the backend tab, type `pytest tests/ -q`
3. If something broke, tell Aider: "The tests are failing with: [paste error]"
4. Check off the item in ROADMAP.md

---

## The Current Priority Order

Work through ROADMAP.md top to bottom.
Right now that means: **get the app running first**.

If the app won't start, that's the only thing to fix.
Don't add features until the basics work.

---

## Keeping Context Between Sessions

At the end of each session, update ROADMAP.md:
- Check off what got done
- Add any new issues you found
- Note what you're working on next

Tell Aider: "Update ROADMAP.md to mark [X] as done and add a note that [Y] is the next thing."

---

## Common Aider Commands

| What you want | What to type in Aider |
|---|---|
| Add a file to context | `/add backend/app/routes/sources.py` |
| See what files are loaded | `/ls` |
| Undo the last change | `/undo` |
| Clear and start fresh | `/clear` |
| Quit | `/exit` |
