# What is this?

A robot teammate for your engineering team. You assign it tickets the same way you'd assign them to a person, and it does two things:

1. **Picks up tickets and does the work.** Assign it a ticket in Jira or Linear, and it reads it, asks clarifying questions if something's unclear, writes a plan, writes the code and tests, and opens a pull request — just like a developer would. When someone leaves review comments on that pull request, it reads them, makes the requested changes (or replies if a comment doesn't need a code change), and keeps going back and forth until the pull request is approved and merged.

2. **Notices when something's wrong and reports it.** It watches your live application for signs of trouble — errors, unusual behavior, things breaking. When it spots something, it opens a ticket describing what happened, with enough detail for someone (or the robot itself, from step 1) to act on it.

Put together, those two things close a loop: something breaks → a ticket gets filed automatically → the ticket gets worked and turned into a pull request → a human reviews and merges it.

## Where do you interact with it?

Nowhere new. There's no separate dashboard or app to log into. It lives entirely inside the tools your team already uses:

- **Jira or Linear** — where you assign it tickets, where it asks questions, where it files tickets about problems it noticed.
- **GitHub** — where its pull requests show up, and where you leave review comments the normal way.

If you can review a pull request from a coworker, you already know how to work with it.

## What it doesn't do (yet)

This is early — right now the underlying scaffolding exists, but neither of the two capabilities above is built end to end. Nothing described here should be assumed to work today. For the technical breakdown of what's actually implemented versus planned, see [SKILLS.md](SKILLS.md).

## Why "stateless"?

One design choice worth knowing about, because it shapes how it behaves: this tool deliberately keeps no memory of its own. It doesn't have a private database tracking "what it's doing." Every time it acts, it looks at the ticket and the pull request fresh — the same way a person glancing at a ticket can tell what's already been done and what's left. That means the ticket and the pull request are always the full, accurate record of what happened — nothing is hidden in a system only the robot can see.

## Read next

- [README.md](README.md) — the technical overview
- [SKILLS.md](SKILLS.md) — what's actually built vs. still planned
