# Codex profile edit approval

Codex's adapter renders ordinary confirmation requests as blocking denials.
Previously no reply could satisfy a non-save profile request. The adapter now
offers an exact, single-use approval for one `apply_patch` update of one existing
non-save XML file within the configured profile. Shell commands, adds, deletes,
moves, multi-file edits, saves, other asks and hard denials remain unchanged.

The hook prints the patch and `PROFILE_EDIT_APPROVAL <digest>`. The digest binds
the session, canonical target, operation, current complete file bytes and exact
patch bytes. Only a matching native human message read through the shared
qualified Codex transcript reader can authorize it. A native async reply may
instead select `Approve this exact edit once` when its question contains that
exact approval line. Agent messages, tool output, summaries and generic yeses
cannot authorize the operation. There is no caller-supplied approval flag.

Backups must succeed before an exclusive consumption receipt is created under
the toolkit's private `.claude/profile-approvals/`. Replays and concurrent duplicate
consumers fail closed. Grants expire after 24 hours. Each receipt binds the native
human message ID as well as the edit. A crash or failed tool consumes that grant;
a fresh explicit human reply can authorize a retry. State errors refuse. The
profile is rehashed before consumption.

Limitations: this is transcript evidence, not an OS boundary against an actor with
write access to the host transcript or receipt store. The hook cannot lock a file
against unrelated writers between approval and the host's actual patch operation.
Unit tests do not prove host enforcement. Changed hook deployment still needs
the host's supported trust workflow and an actual native-hook check.

Existing prose approvals are not retroactively converted into hash-bound grants.
This protocol changes neither save preservation nor the shared bulk-save reader.

Official host reference: [OpenAI Hooks documentation](https://learn.chatgpt.com/docs/hooks),
checked October 28, 2026, still documents PreToolUse `ask` as unsupported. The
adapter therefore retains its native deny transport instead of returning `ask`.
