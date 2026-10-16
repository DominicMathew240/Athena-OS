"""Inference apps: operator-configured screens over one approved model, with human feedback.

Predictions run in ``manage.py inference_worker`` (same queue pattern as auto-labelling), so the
web process never imports torch. Feedback is recorded, never learned from automatically.
"""
