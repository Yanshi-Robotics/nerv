from types import SimpleNamespace
from nerv.body.stopping import QuietWindow


def state(t, x=0, joint=0):
    return SimpleNamespace(t=t, odom_xy=[x,0], base_height=1, imu_gyro=[0,0,0], joint_vel=[joint])


def test_standing_confirmation_is_revoked_by_sliding_or_bad_measurement():
    window = QuietWindow(joint_limit=.3, linear_limit=.05, angular_limit=.1, duration=.2, max_age=1)
    for t in [0,.1,.2,.31]:
        result = window.update(state(t))
    assert result['stop_confirmed']
    assert not window.update(state(.4,.1))['stop_confirmed']
    assert not window.update(state(.5,.1,float('nan')))['stop_confirmed']
    for t in [.6,.7,.8,.91]:
        result = window.update(state(t,.1))
    assert result['stop_confirmed']
    assert not window.update(state(.1,.1))['stop_confirmed']


def test_same_timestamp_cannot_hide_fall_or_motion():
    for fault in ['fall', 'velocity', 'position']:
        window = QuietWindow(joint_limit=.3, linear_limit=.05, angular_limit=.1, duration=.2, max_age=1)
        for t in [0,.1,.2,.31]:
            result = window.update(state(t))
        assert result['stop_confirmed']
        changed = state(.31, x=.1 if fault == 'position' else 0, joint=1 if fault == 'velocity' else 0)
        assert not window.update(changed, fallen=fault == 'fall')['stop_confirmed']
