---
title: Veto
emoji: "\U0001F6D1"
colorFrom: gray
colorTo: blue
sdk: docker
app_port: 8787
pinned: false
short_description: The consent layer between an AI agent and the company wallet
---

# Veto

Agents spend. Humans govern. Veto is the consent layer between an AI agent and
the company wallet: every cart is judged against the agent's charter, a human
signs what the rules do not cover, and only then does PayPal move money.

- **Live console:** open the Space URL. The seeded demo company is
  *Bracken & Field*; the demo button signs you in as its approver.
- **Slack:** `/veto`, `/veto-check`, `/veto-status`, `/veto-budget`,
  `/veto-routines`, `/veto-help` all point at `https://<space-host>/api/slack/veto`.
- **Money:** PayPal sandbox only. A captured request is a real sandbox
  authorization and capture, with its ids on the record.

This Space is the public demo for the PayPal AI Hackathon entry. The image is
built from the repository root by the sibling `Dockerfile`; secrets arrive as
Space secrets, never in the image.
