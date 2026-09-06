from radar_sim.echo import TargetSceneManager


def test_remove_target_removes_one_matching_target_and_preserves_order():
    scene = TargetSceneManager()
    scene.addTarget(5_000.0)
    scene.addTarget(25_000.0)
    scene.addTarget(5_000.0, rcs=2.0)

    assert scene.removeTarget(5_000.0) is True
    assert [target.distance for target in scene.targets] == [25_000.0, 5_000.0]
    assert scene.removeTarget(99_000.0) is False


def test_remove_target_is_reflected_in_delays():
    scene = TargetSceneManager()
    scene.addTarget(5_000.0)
    scene.addTarget(25_000.0)
    scene.removeTarget(5_000.0)

    delays = scene.getTargetDelays(10e6)
    assert delays.shape == (1,)
    assert delays[0] > 0
