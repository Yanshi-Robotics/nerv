"""Text a person reads (the model's text lives in brain/prompts.py)."""
from __future__ import annotations

MAX_STEPS_REPLY = ('(I have taken a good many steps on this without finishing, so I am pausing '
                   'here. Say "continue" and I will carry on.)')
TIME_BUDGET_REPLY = '(This turn reached its time limit, so I am pausing here. Say "continue" to carry on.)'
INTERRUPTED_REPLY = '(Stopped. Say "continue" and I will pick up where I left off.)'
STOP_REPLIES = {"steps": MAX_STEPS_REPLY, "time": TIME_BUDGET_REPLY, "interrupt": INTERRUPTED_REPLY}

UNKNOWN_BRAIN_REPLY = "(That brain is not one this platform knows about.)"
BRAIN_NOT_CONFIGURED_REPLY = "(That brain is not configured. Set it up in .env and restart.)"
BRAIN_CALL_FAILED_LEAD = "(The brain call failed.)"
BRAIN_CALL_FAILED_REPLY = BRAIN_CALL_FAILED_LEAD + " {error}"
TELEOP_STEP = "(operator teleop: {name})"
SESSION_NOT_ACTIVE = "(This session is {status}; start a new one to continue.)"
