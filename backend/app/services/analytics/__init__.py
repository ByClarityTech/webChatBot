"""Conversation analytics: outcome labels, metrics, the labeler, and zero-PHI exports.

outcomes, metrics, filters, csv_export and transcript are pure (no database). The SQL lives in
app/db/models/analytics.py, and every query there uses the one shared test-session predicate.
"""
