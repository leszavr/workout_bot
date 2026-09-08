"""Контракт controlled set для воспроизводимого generation benchmark."""
from scripts.run_generation_benchmark import SCENARIOS, build_profiles


def test_controlled_set_has_twenty_unique_valid_profiles():
    profiles = build_profiles()

    assert len(profiles) == 20
    assert [scenario.benchmark_id for scenario, _ in profiles] == [
        f"BENCH-{number:03d}" for number in range(1, 21)
    ]
    assert len({profile.profile_id for _, profile in profiles}) == 20
    assert all(profile.source.bot_user_id is None for _, profile in profiles)


def test_repeatability_selection_rejects_unknown_profiles():
    selected = build_profiles({"BENCH-003", "BENCH-016", "BENCH-020"})

    assert [scenario.benchmark_id for scenario, _ in selected] == [
        "BENCH-003",
        "BENCH-016",
        "BENCH-020",
    ]

    try:
        build_profiles({"BENCH-999"})
    except ValueError as exc:
        assert "BENCH-999" in str(exc)
    else:
        raise AssertionError("Unknown benchmark profile must be rejected")
