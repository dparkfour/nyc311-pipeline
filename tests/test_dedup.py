from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.pipeline import dedup

UTC = timezone.utc
T0 = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def rec(key: str, minutes: int, ctype="NOISE - RESIDENTIAL", address="55 W 21 ST") -> dict:
    return {
        "unique_key": key,
        "complaint_type_norm": ctype,
        "address_norm": address,
        "created_date": T0 + timedelta(minutes=minutes),
    }


class TestDedupKey:
    def test_type_and_address_form_the_key(self):
        assert dedup.dedup_key(rec("a", 0)) == ("NOISE - RESIDENTIAL", "55 W 21 ST")

    def test_a_record_without_an_address_cannot_be_grouped(self):
        """Otherwise every addressless complaint in the city collapses into
        one -- the over-aggressive failure this design exists to avoid."""
        assert dedup.dedup_key({"complaint_type_norm": "X", "address_norm": None}) is None
        assert dedup.dedup_key({"complaint_type_norm": None, "address_norm": "Y"}) is None


class TestCollapse:
    def test_two_reports_inside_the_window_collapse(self):
        links = dedup.collapse([rec("a", 0), rec("b", 20)], window_minutes=60)
        assert len(links) == 1
        assert links[0].unique_key == "b"
        assert links[0].duplicate_of == "a"
        assert links[0].minutes_apart == 20.0

    def test_reports_outside_the_window_are_left_alone(self):
        assert dedup.collapse([rec("a", 0), rec("b", 90)], window_minutes=60) == []

    def test_the_earliest_record_survives(self):
        links = dedup.collapse([rec("late", 30), rec("early", 0)], window_minutes=60)
        assert links[0].duplicate_of == "early"

    def test_different_complaint_types_at_one_address_are_distinct(self):
        links = dedup.collapse(
            [rec("a", 0), rec("b", 5, ctype="ILLEGAL PARKING")], window_minutes=60
        )
        assert links == []

    def test_the_same_complaint_at_different_addresses_is_distinct(self):
        links = dedup.collapse(
            [rec("a", 0), rec("b", 5, address="57 W 21 ST")], window_minutes=60
        )
        assert links == []

    def test_a_chain_of_reports_collapses_into_one_survivor(self):
        """Four neighbours reporting one hydrant do not arrive on a schedule.
        A at 10:00, B at 10:45, C at 11:20 all collapse into A even though A
        and C are 80 minutes apart -- requiring every member to sit inside the
        first record's window splits a real cluster in two."""
        links = dedup.collapse(
            [rec("a", 0), rec("b", 45), rec("c", 80)], window_minutes=60
        )
        assert {l.duplicate_of for l in links} == {"a"}
        assert {l.unique_key for l in links} == {"b", "c"}

    def test_a_gap_wider_than_the_window_starts_a_new_cluster(self):
        links = dedup.collapse(
            [rec("a", 0), rec("b", 30), rec("c", 200), rec("d", 220)],
            window_minutes=60,
        )
        by_key = {l.unique_key: l.duplicate_of for l in links}
        assert by_key == {"b": "a", "d": "c"}

    def test_result_is_deterministic_regardless_of_input_order(self):
        """This runs every fifteen minutes over overlapping windows, so an
        order-dependent survivor would rewrite is_duplicate_of on every run."""
        records = [rec("a", 0), rec("b", 10), rec("c", 20)]
        forward = dedup.collapse(list(records), 60)
        backward = dedup.collapse(list(reversed(records)), 60)
        assert sorted((l.unique_key, l.duplicate_of) for l in forward) == \
               sorted((l.unique_key, l.duplicate_of) for l in backward)

    def test_simultaneous_records_break_ties_by_key(self):
        links = dedup.collapse([rec("zzz", 0), rec("aaa", 0)], window_minutes=60)
        assert links[0].duplicate_of == "aaa"

    def test_records_missing_a_created_date_are_skipped(self):
        bad = rec("x", 0)
        bad["created_date"] = None
        assert dedup.collapse([bad, rec("a", 0)], window_minutes=60) == []

    def test_a_narrow_window_collapses_strictly_less_than_a_wide_one(self):
        """The monotonicity that makes `cli tune-dedup` meaningful."""
        records = [rec(str(i), i * 10) for i in range(8)]
        counts = [len(dedup.collapse(list(records), w)) for w in (5, 15, 60, 1440)]
        assert counts == sorted(counts)

    def test_cluster_sizes_reports_the_largest_group(self):
        links = dedup.collapse([rec(str(i), i * 5) for i in range(5)], window_minutes=60)
        sizes = dedup.cluster_sizes(links)
        assert sizes == {"0": 4}
