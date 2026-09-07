"""ESE 6150 leaderboard builder.

Reads graded submissions from every student repo of a lab, keeps each
student's best on-time full-score lap under a racing alias, writes the
public data files the page reads, tells each student their alias in their
own Feedback PR, and locks a repo once it has spent its submission cap.
"""
