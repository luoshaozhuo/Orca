undefined
@pytest.mark.asyncio
async def test_write_gate_rechecked_after_strategy():
    commander = FakeCommander()
    cycle = ControlCycle(load_config(CONFIG), StaticSource(), commander, FixedStrategy())
    checks = iter((True, False))
    reason = await cycle.execute(
        "active_power", can_write=lambda: next(checks),
    )
    assert "dependency state changed" in reason
    assert commander.calls == []


@pytest.mark.asyncio
async def test_invalid_input_never_calls_strategy():
    class InvalidSource(StaticSource):
        async def latest(self, pairs):
            result = await super().latest(pairs)
            for item in result.values():
                item["value"] = float("nan")
            return result
    class Forbidden:
        def solve(self, measurements):
            raise AssertionError("invalid input reached strategy")
    commander = FakeCommander()
    cycle = ControlCycle(load_config(CONFIG), InvalidSource(), commander, Forbidden())
    assert "non-finite input" in await cycle.execute("active_power", allow_write=True)
    assert not commander.calls
