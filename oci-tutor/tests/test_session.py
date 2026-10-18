from tutor import TutorSession


class LeakyModel:
    def generate_hint(self, approved_hint, rung):
        return "The answer is 5/6"


class BrokenModel:
    def generate_hint(self, approved_hint, rung):
        raise TimeoutError("OCI timed out")


def test_hints_go_1_2_3_and_stop():
    s = TutorSession()
    assert [s.handle("hint")["rung"] for _ in range(4)] == [1, 2, 3, 3]


def test_empty_does_not_advance():
    s = TutorSession()
    assert s.handle("")["rung"] == 0


def test_answer_request_does_not_reveal_or_advance():
    s = TutorSession()
    result = s.handle("just tell me the answer")
    assert "5/6" not in result["reply"]
    assert result["rung"] == 0


def test_answer_request_repeats_current_hint():
    s = TutorSession()
    s.handle("hint")
    assert "Look at the bottom numbers" in s.handle("give me the answer")["reply"]


def test_correct_answer_is_recognized():
    s = TutorSession()
    assert s.handle("5/6")["status"] == "solved"
    assert TutorSession().handle("is it 5 / 6?")["status"] == "solved"


def test_leaky_model_is_blocked():
    s = TutorSession(model=LeakyModel())
    assert "5/6" not in s.handle("hint")["reply"]


def test_model_failure_falls_back():
    s = TutorSession(model=BrokenModel())
    assert s.handle("hint")["reply"].startswith("Look at the bottom numbers")


def test_events_logged_for_teacher_view():
    s = TutorSession()
    s.handle("hint")
    s.handle("just tell me")
    s.handle("5/6")
    assert [e["event"] for e in s.events] == ["hint_request", "answer_request", "solved"]


def test_wrong_answer_gets_feedback_not_praise():
    s = TutorSession()
    reply = s.handle("2")["reply"]
    assert "Good question" not in reply
    assert "fraction" in reply
    assert s.events[-1]["event"] == "wrong_answer"


def test_common_mistake_gets_targeted_feedback():
    s = TutorSession()
    assert "added the top numbers and the bottom numbers" in s.handle("2/5")["reply"]


def test_mistake_feedback_never_leaks_answer():
    s = TutorSession()
    for wrong in ("2/5", "2/6", "3", "1/5", "0.5"):
        assert "5/6" not in s.handle(wrong)["reply"]


def test_real_question_still_general():
    s = TutorSession()
    assert "Not quite" not in s.handle("what does denominator mean")["reply"]


def test_wrong_answer_feedback_by_size():
    s = TutorSession()
    assert "one whole or more" in s.handle("7/6")["reply"]
    assert "should get bigger" in s.handle("1/4")["reply"]
    assert "right range" in s.handle("4/6")["reply"]


def test_all_feedback_and_hints_never_leak():
    s = TutorSession()
    replies = [s.handle(m)["reply"] for m in ("2", "1/4", "4/6", "7/6", "hint", "hint", "hint")]
    for r in replies:
        assert "5/6" not in r and "five sixths" not in r.lower()


def test_after_last_hint_points_to_teacher():
    s = TutorSession()
    for _ in range(3):
        s.handle("hint")
    result = s.handle("hint")
    assert result["status"] == "needs_teacher"
    assert "teacher" in result["reply"]
    assert s.events[-1]["event"] == "needs_teacher"


def test_safety_message_gets_fixed_script_first():
    s = TutorSession(model=LeakyModel())
    result = s.handle("someone at home is hurting me")
    assert result["status"] == "safety_concern"
    assert "grown-up you trust" in result["reply"]
    assert s.events[-1]["event"] == "safety_concern"


def test_rung_3_is_a_different_worked_example():
    s = TutorSession()
    for _ in range(3):
        reply = s.handle("hint")["reply"]
    assert "1/4 + 2/4 = 3/4" in reply
