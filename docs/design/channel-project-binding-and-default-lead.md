# Channel ↔ Project Binding and Default Lead

> Status: **proposed** · 2026-07-28
> Extends [agent-channel-routing.md](agent-channel-routing.md), which fixed the
> first rule of the channel layer — *a channel belongs to an agent identity; the
> project is resolved at message time*. This note answers the two questions that
> rule left open once people actually work in an IM group:
>
> 1. How does a Feishu/WeCom group come to mean one Valuz project?
> 2. When a project holds a team of agents, who answers, and who leads a task?

---

## 1. Problem

A Valuz project is a **team**: several member agents, plus tasks that need a
**lead**. A chat in the app picks an agent explicitly; creating a task picks a
lead explicitly. An IM group has neither picker — there is only a text box.

Today the channel layer collapses three distinct roles into one:

| Role | What it decides | Today |
|---|---|---|
| Channel identity | who represents Valuz inside Feishu | the bot = one Feishu app |
| Conversation agent | who answers this message | hard-wired to the app's bound agent |
| Task lead | who leads a multi-step task | defaults to the conversation agent |

The visible consequence: pull the helper bot into a project group and it becomes
the lead of everything, regardless of which member agent is actually suited to
the work.

Two shapes were rejected before landing on this design:

- **One bot per project** — a bot is an *app*: created in the Feishu admin
  console, permissioned, published, and its credentials pasted into Valuz. That
  is an administrator action, while projects are created casually. N projects
  would mean N apps, N long connections, N credential sets.
- **A default lead stored on the channel binding** — the "who leads" question is
  not a property of the chat. It is a property of the project, and the desktop
  app needs the same answer when creating a task from the UI.

## 2. Model

**A group is a project. A bot is an entrance, not a worker. A project names its
own default lead.**

```
Feishu group ──binds to──► Valuz project ──names──► default lead agent
     │                          │
     │                          └── members: agent A, agent B, helper …
     │
     └── bot (app) = the entrance; the agent that answers is resolved per turn
```

Direct 1:1 chats with the bot stay what they are today: a personal quick chat,
with no project (see the quick-chat fallback in `AgentChannelResolver`).

### 2.1 What already supports this

Verified against the current implementation — none of it needs redesign:

- The route key is
  `(channel_instance_id, external_chat_id, external_thread_id, agent_slug, project_id)`.
  **Both `agent_slug` and `project_id` are already in the key**, so one chat
  keeps an independent session lineage per (agent, project). Switching agent or
  project is switching lineage; switching back resumes the earlier session
  rather than starting over.
- `ChannelThreadBindingDatastore.get_for_thread` returns the most recently
  updated lineage for the chat, which makes "the last thing you worked on" the
  natural continuation target.
- `_extract_project_hint` already parses `项目：X` / `project: X` from message
  text, and `AgentChannelResolver` matches it against placement project names
  with normalization.
- With several placements and no hint, the resolver returns `ASK_PROJECT` and
  the runner replies with the candidate project names.
- `create_task` already accepts a `lead_agent` argument — a task's lead does not
  have to be the conversation agent.
- The Feishu SDK exposes everything the binding flows need: `im.v1.chat.alist`
  (groups the bot is in), `im.v1.chat.aget` (group name),
  `im.chat.member.bot.added_v1` (bot pulled into a group), and
  `card.action.trigger` (card button callbacks).

### 2.2 What is missing

1. No persistent **chat → project** binding. Project membership of a chat is
   inferred from "most recently used lineage", which cannot be inspected,
   changed, or reasoned about.
2. No **project default lead**. `create_task` falls back to the conversation
   agent, which is why the helper leads everything.
3. No way to **choose the answering agent** in a chat: `agent_slug` comes from
   the app binding and never varies.

---

## 3. Data model

### 3.1 Project default lead (project module)

```
valuz_project
  + default_lead_agent_slug: str | None     -- project-local member slug
```

Stored on the **project row**, not as an `is_default_lead` flag on
`valuz_project_member`. "A project has at most one default lead" is then true by
construction; a flag on the member table would need a partial unique index or
application-level upkeep, and would fail open to two leads or none.

Dangling-pointer rules:

- On read, verify the slug is still a member of that project; if not, fall
  through the resolution chain (§4.3) as if unset.
- On member removal, clear the column when it pointed at the removed member.

This column is **not channel-specific**. The desktop task-creation UI should
preselect it too — that is the main reason it belongs to the project model
rather than to a channel binding.

### 3.2 Chat → project binding (channels module)

```
valuz_channel_chat_binding
  user_id                str      -- owner (the Valuz install)
  channel_instance_id    str
  external_chat_id       str
  project_id             str
  default_agent_slug     str|None -- who answers by default in this chat
  external_chat_name     str|None -- cached for display in the Valuz UI
  bound_by_external_user str|None -- audit: which IM user bound it
  UNIQUE (user_id, channel_instance_id, external_chat_id)
```

One chat binds to exactly one project — otherwise "group = project" does not
hold. A project may be bound from several groups (an internal group and a client
group, say); their sessions stay independent and do not share context, which the
UI must state plainly.

`default_agent_slug` is optional and defaults to the app binding's agent. It
exists so a group can be pointed straight at a specialist ("this group talks to
the analyst") instead of always entering through the helper.

---

## 4. Resolution

### 4.1 Project

```
explicit hint in the message (项目：X)
  > chat → project binding
  > single placement of the mentioned agent (auto)
  > several placements → ASK_PROJECT (reply lists candidates)
  > no placement → quick chat (ephemeral chat project)
```

The binding slots in above the placement heuristics: once a group is bound,
placement ambiguity stops being a question anyone is asked.

### 4.2 Answering agent

```
agent named in the message ("让分析师看看这个")
  > chat binding's default_agent_slug
  > the app binding's agent (today's behaviour)
```

Naming an agent switches lineage, so each agent keeps its own thread of
conversation inside the group and switching back resumes it. Parsing mirrors
`_extract_project_hint`: a small `_extract_agent_hint` matching member slugs and
display names of the resolved project, normalized the same way. A card with
member buttons is the non-typing equivalent and shares the resolution path.

### 4.3 Task lead

```
lead named by the user / picked in a card
  > project.default_lead_agent_slug (when still a member)
  > the conversation agent            (today's behaviour, kept as a floor)
```

Applies to **both** launcher paths — `create_task` (argument `lead_agent`) and
`draft_task` (argument `lead_agent_slug`, currently required). They must share
one resolution helper; two launchers disagreeing about who leads is the kind of
inconsistency that is very hard to diagnose from a chat transcript.

When a project has no default lead and the conversation agent is the helper, the
turn still runs with the helper as lead. Refusing would break the most common
first contact ("pull the bot in and ask something"); instead the project page
flags projects with no default lead.

---

## 5. Binding flows

Three ways to establish a group ↔ project mapping, in order of priority.

### A. From the Valuz project page (primary)

Project detail gains a *Feishu group* control listing the groups the bot is in
(`im.v1.chat.alist` → chat id + name); pick one to bind. Binding is a
configuration act, and only the Valuz side has the global view: which project is
bound to which group, and the ability to rebind or unbind.

Full user journey: **create the group in Feishu → add the bot → return to the
Valuz project page → pick that group.**

### C. Guided binding when the bot joins (best first-run experience)

Subscribe to `im.chat.member.bot.added_v1`. The moment the bot is added to a
group it posts a card — "Which project should this group work on?" — with a
button per project. The click arrives as `card.action.trigger`, writes the
binding, and the card updates in place to "bound to X". No typing, no switching
back to the desktop app.

### B. Text commands (cheap complement)

`绑定项目 X` · `当前项目` · `解绑`, reusing the existing normalized project-name
matching. On a miss, reply with the candidate card from flow C rather than an
error.

Recommended scope: **A + C together** (C is the on-ramp, A is the manageable
surface), with B added because it costs almost nothing.

---

## 6. The helper agent's role

With a project default lead in place, the helper becomes a **receptionist**:
it relays work and reports status, and does not execute.

Its tool set already exists — `create_task` (hand off), `list_tasks` /
`get_task` (report progress). Two things make the role real:

1. the lead resolution chain of §4.3, so handing off without naming a lead
   reaches the project's default lead instead of the helper itself;
2. instructions that say plainly: *you do not perform the work yourself; on a
   work request call `create_task`; answer only status and information
   questions*.

If the helper should also answer "who is on this project / what files are here /
what is in the knowledge base", that needs read tools beyond the three above.
Not a blocker — it can follow.

---

## 7. Permission boundary (decide before shipping groups)

The channel binding row carries the `user_id` of the Valuz install. **Every
message from the group executes as that user** — reading and writing that
person's project directory, spending their model quota, touching their knowledge
base. Adding the bot to a group therefore means opening those projects to every
member of the group.

Acceptable for single-user self-hosting; a real boundary for a team group. At
minimum:

- state it at binding time, in both flow A and flow C;
- restrict who may bind — for example only commands from an IM account
  associated with the Valuz owner (via `external_user_id` and a one-time binding
  code) may create or change a binding.

A finer model (per-IM-user identity mapped to Valuz users, per-project sharing)
is out of scope here and belongs to the commercial identity layer.

---

## 8. Out of scope: one app, many agents

A Feishu app still binds to exactly one agent
(`valuz_agent_channel_binding` is unique on `(user_id, platform, agent_slug)`,
one long connection per binding). So a project group wanting several agents to
be individually addressable as bots would need one app per agent — the same
cost problem as one bot per project, on a different axis.

This design deliberately avoids depending on that: agent selection happens
*inside* one bot's conversation (§4.2), not by mentioning different bots. Making
one app carry many agents is a larger change to the binding model and message
routing, worth doing on its own merits later.

---

## 9. Implementation phases

| Phase | Scope | Notes |
|---|---|---|
| 1 | Project default lead: column + migration, shared lead-resolution helper wired into `create_task` and `draft_task`, project-page selector, clear-on-member-removal | Independent of channels; the desktop task UI benefits immediately |
| 2 | `valuz_channel_chat_binding` + project resolution order (§4.1) + flow A (project page ↔ `chat.alist`) | Makes "group = project" real and inspectable |
| 3 | Flow C: `im.chat.member.bot.added_v1` subscription, project-picker card, `card.action.trigger` handling; flow B commands | Needs new event subscriptions and permissions on the Feishu app |
| 4 | Agent selection (§4.2): `_extract_agent_hint`, member-picker card, `default_agent_slug` | Reuses the existing per-agent session lineage |

Phase 1 is the smallest and pays off outside the channel layer, so it goes
first.

## 10. Related

- [agent-channel-routing.md](agent-channel-routing.md) — the routing contract
  this extends; also documents the session model (one chat conversation, one
  long-lived session; `explicit_new_hint` resets; a user-opened topic branches).
- `modules/channels/{resolver,service,datastore}.py` — routing, ingress, bindings
- `modules/tasks/tools/{declarations,handlers}.py` — `create_task` / `draft_task`
- `integrations/feishu_long_connection.py` — long connection, reaction
  acknowledgement, streaming card output
