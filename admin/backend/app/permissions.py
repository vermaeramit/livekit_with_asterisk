"""What a role may do. The whole list, in one place.

Derived from what the routers actually guard rather than invented: every entry
below corresponds to something a request is refused for today, or to a piece of
the console that ought to be refusable and was not.

Two of them are new and are the reason this exists at all:

  calls.recording  reading a transcript and LISTENING to the caller are not the
                   same permission, and a system that records every call should
                   not hand the audio to everyone who can log in.
  cost.read        an agent has no business knowing that a call cost Rs 8.

Deliberately about a dozen and not two dozen. A list fine enough to express
"may read the campaign but not the prompt" is a list nobody sets correctly, and
a permission nobody sets correctly protects nothing.
"""
from __future__ import annotations

# key -> (group, label, what it lets someone do)
#
# The label and description are shown on the roles page. They are here rather
# than in the frontend so that adding a permission is one edit, and so the
# console can never offer a permission the backend does not enforce.
PERMISSIONS: dict[str, tuple[str, str, str]] = {
    # ---- reading ----
    "calls.read": (
        "Calls", "Calls and transcripts",
        "See the call list, transcripts, timings and which tools ran."),
    "calls.recording": (
        "Calls", "Listen to recordings",
        "Play the audio of a call. Separate from reading it: the transcript is "
        "a record, the recording is the caller's voice."),
    "analytics.read": (
        "Calls", "Dashboard",
        "Call volume, latency and outcomes."),
    "usage.read": (
        "Calls", "Usage",
        "Tokens, characters and audio seconds a call consumed. Held apart from "
        "the transcript and together with cost, because anyone holding the "
        "rates can work the price out from these."),
    "cost.read": (
        "Calls", "Costs",
        "What calls cost, per call and on the dashboard."),
    "alerts.read": (
        "Calls", "Alerts",
        "See alerts and acknowledge them. Editing the rules that raise them is "
        "part of editing a campaign."),
    "live.read": (
        "Calls", "Live monitor",
        "Watch calls while they are happening."),
    "gaps.read": (
        "Calls", "Knowledge gaps",
        "Questions the agent could not answer, and marking them handled."),

    # ---- a client's own configuration ----
    "campaign.write": (
        "Campaigns", "Edit campaigns",
        "The prompt, knowledge base, voice, tools, routing and alerts."),
    "provider_keys.write": (
        "Campaigns", "Provider keys",
        "Set the API keys a campaign's calls are billed to. Separate from "
        "editing a campaign: this one spends money."),
    "users.manage": (
        "Campaigns", "Manage users",
        "Invite, deactivate and assign roles within the client."),

    # ---- the platform ----
    "tenants.manage": (
        "Platform", "Manage clients",
        "Create and suspend clients."),
    "rates.manage": (
        "Platform", "Provider rates",
        "Set what each provider charges and the exchange rate."),
    "system.manage": (
        "Platform", "Backups and system",
        "Backup status, running a backup, platform acknowledgements."),
}

GROUPS = ("Calls", "Campaigns", "Platform")

# What each seeded role starts with.
#
# These reproduce the access people have TODAY, exactly - migration day changes
# nothing, and tightening is a decision somebody makes afterwards with the
# console in front of them rather than a surprise they discover from a support
# call.
#
# So `agent` and `viewer` are identical here, because they are identical today:
# both roles exist, and neither has ever meant anything different from the
# other. Making them differ is the first useful thing to do with this feature,
# and it is not mine to decide.
SEED_ROLES: tuple[tuple[str, str, str, bool, tuple[str, ...]], ...] = (
    ("superadmin", "Super Admin",
     "Everything, across every client. Cannot be edited.",
     True, tuple(PERMISSIONS)),
    ("tenant_admin", "Admin",
     "Runs one client: campaigns, keys and users.",
     False, ("calls.read", "calls.recording", "analytics.read", "usage.read",
             "cost.read", "alerts.read", "live.read", "gaps.read",
             "campaign.write",
             "provider_keys.write", "users.manage")),
    ("agent", "Agent",
     "Reads calls and dashboards. Changes nothing.",
     False, ("calls.read", "calls.recording", "analytics.read", "usage.read",
             "cost.read", "alerts.read", "live.read", "gaps.read")),
    ("viewer", "Viewer",
     "Reads calls and dashboards. Changes nothing.",
     False, ("calls.read", "calls.recording", "analytics.read", "usage.read",
             "cost.read", "alerts.read", "live.read", "gaps.read")),
)


def valid(perms) -> list[str]:
    """Keep only permissions this build knows about, in a stable order.

    A permission the backend does not enforce is worse than a missing one: the
    console would show it ticked and it would guard nothing at all.
    """
    known = [p for p in PERMISSIONS if p in set(perms or ())]
    return known
