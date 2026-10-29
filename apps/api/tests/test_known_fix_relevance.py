from __future__ import annotations


def test_known_fix_relevance_rejects_generic_notepad_match_for_disk_request():
    from joltiq.services import agent

    assert hasattr(agent, "_known_fix_relevant"), "known-fix sharing needs an explicit relevance gate"
    query = "Please check this Windows PC's free disk space using a read-only diagnostic and tell me the exact result."
    notepad = (
        "Learned fix: Run this exact harmless typing test in Notepad. "
        "Resolution: Verified on screen that the exact JoltIQ acceptance sentence is visible."
    )

    assert agent._known_fix_relevant(query, notepad) is False


def test_known_fix_relevance_accepts_same_problem_and_symptom():
    from joltiq.services import agent

    assert hasattr(agent, "_known_fix_relevant"), "known-fix sharing needs an explicit relevance gate"
    query = "My HP printer shows offline on my laptop"
    prior = "Printer shows offline. HP printer offline on laptop. Resolution: Updated the TCP/IP port."

    assert agent._known_fix_relevant(query, prior) is True


def test_known_fix_relevance_rejects_matching_goal_with_unrelated_resolution():
    from joltiq.services import agent

    query = "Please check this Windows PC's free disk space using a read-only diagnostic."
    prior_problem = "Please check this Windows PC's free disk space using a read-only diagnostic."
    unrelated_resolution = "Typed the note and saved it (fixture script)."

    assert agent._known_fix_relevant(query, prior_problem, unrelated_resolution) is False


def test_known_fix_relevance_accepts_matching_problem_and_resolution():
    from joltiq.services import agent

    query = "My HP printer shows offline on my laptop"
    prior_problem = "HP printer offline on laptop"
    resolution = "Printer IP changed; updated the TCP/IP port."

    assert agent._known_fix_relevant(query, prior_problem, resolution) is True
