from app.agent_base.core.subagent_budget import CumulativeTokenBudget


def test_request_reserves_a_second_input_before_allowing_exploration():
    policy = CumulativeTokenBudget(131072, 3000)

    assert not policy.allowance(0, 10000, finalizing=False).finalize
    assert policy.allowance(110000, 10000, finalizing=False).finalize
    final = policy.allowance(110000, 4000, finalizing=True)
    assert final.output_tokens > 0
    assert final.input_tokens + final.output_tokens <= policy.remaining(110000)


def test_actual_input_underestimate_reduces_future_output_allowance():
    policy = CumulativeTokenBudget(12000, 3000)
    before = policy.allowance(7000, 2000, finalizing=True)

    policy.observe_input(actual=4000, estimated=2000)
    after = policy.allowance(7000, 2000, finalizing=True)

    assert 0 < after.output_tokens < before.output_tokens
    assert after.input_tokens + after.output_tokens <= policy.remaining(7000)
    assert policy.allowance(12000, 2000, finalizing=True).output_tokens == 0
