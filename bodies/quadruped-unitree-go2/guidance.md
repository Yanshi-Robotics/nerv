# Unitree Go2 simulation

Use the forward camera and measured motion feedback. Move and turn skills are
bounded by the selected deployment profile; observe again after every skill.
Do not use scene coordinates or room labels as sensor observations.

The historical Go2 policy is missing its training command ranges and deadband.
Its motion and stop profile also needs validation. Select a complete, checked
Go2 release before starting motion. Weight loading alone does not verify walking,
obstacle stopping, supervision loss, or hardware behavior.

Real Go2 control is not supported. A simulation reset starts a new physical state;
it does not prove the previous task succeeded.
