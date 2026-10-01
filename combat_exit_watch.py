"""Read-only threat settlement after a runtime error; no target acquisition."""
from threat_settlement import QuietPeriod
from threat_state import ThreatMonitor
from runtime_types import Snapshot
class ExitWatch:
 def __init__(self,started):
  self.started=started;self.hp=None;self.last_hp=None;self.last_sequence=None
  self.quiet=QuietPeriod()
  self.monitor=ThreatMonitor();self.synthetic_sequence=0
 def step(self,observation,combat_known,age,now,sequence=None,recent_incoming=False,threat_state=None):
  o=observation
  # Polling can revisit a frame after it has aged past the freshness limit.
  # A duplicate is no new evidence: it must neither advance nor erase the
  # quiet period established by fresh, distinct frames.
  if sequence is not None and sequence==self.last_sequence:return 'observe'
  if not o.valid or not combat_known or not 0<=age<=.5:
   self.quiet.step(now,sequence,valid=False)
   self.last_sequence=sequence
   return 'observe'
  self.last_sequence=sequence
  if self.hp is None:self.hp=o.player_hp
  # Existing damage is not ongoing damage. Compare consecutive fresh readings.
  if threat_state is None:
   self.synthetic_sequence+=1
   threat_state=self.monitor.update(Snapshot(sequence if sequence is not None else self.synthetic_sequence,
       now-age,(),o,None,combat_known=combat_known),now)
  damaged=threat_state.damaged
  self.last_hp=o.player_hp
  if threat_state.active or recent_incoming:
   self.quiet.step(now,sequence,threat=True)
   # A text hint alone may delay declaring peace, but never authorizes movement.
   if threat_state.visual:
    if (threat_state.additional_suspected or o.player_hp<.75
        or threat_state.episode_drop>.08 or o.player_hp<self.hp-.08
        or now-self.started>=8):return 'escape'
   return 'observe'
  return 'peace_confirmed' if self.quiet.step(now,sequence) else 'observe'
