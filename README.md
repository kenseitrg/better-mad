# better-mad

Agent-driven visualization of seismic attributes: load large tabular text files,
describe the plot you want to an LLM agent (any harness, running in an embedded
terminal), and get back an editable plotting script with a live interactive preview.

- **design.md** — v2 spec & decisions (agent + script + preview model)
- **UX.md** — how the user interacts with it
- **PLAN.md** — implementation milestones and progress
- **AGENTS.md** — orientation for AI-assisted development

The v1 declarative plotting implementation (milestones M0–M4) is archived on the
`archive/codebase` branch.

## Quick start (once M2 lands)

```bash
uv sync
uv run better-mad data/14_01_post_stack_attr_after_scac_all
```
