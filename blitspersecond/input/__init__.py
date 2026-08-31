"""Input devices and their tick-settled facts.

Raw backends write only ingress queues.  The engine's frame boundary turns
those queues into immutable history.  Public device vocabulary is a later
layer, so this package exports no keyboard facade yet.
"""
