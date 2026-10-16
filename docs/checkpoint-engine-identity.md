# Dashboard checkpoint source identity

checkpoint_campaign.engine_identity uses an explicit ENGINE_SOURCES tuple.
The hash includes each listed filename and its source bytes, in a fixed order.
The dependency set now includes mutation_graph.py and usb_speeds.py, so changes
to delegated mutation operators or speed policy alter the expected engine_id.
Missing listed files raise an error; there is no fallback to a partial hash.
The identity is independent of the checkout's absolute directory.

This is the source-fingerprint checkpoint mechanism used by
run_checkpoint_session and campaign-checkpoint.json. Existing dashboard
checkpoints made with the previous source identity will fail the strict
engine_id comparison: start a new campaign or replay a saved configuration.
The fix does not rewrite old checkpoint files or existing results.

This change does not modify the mutation algorithm, campaign execution flow,
checkpoint JSON schema, or the separate UART/SSH v3 checkpoint format. It is
not an assertion of hardware compatibility or a successful full regression run.
Third-party protocol plugin code is not made hermetic by this explicit source
list and still requires separate version/environment control.
