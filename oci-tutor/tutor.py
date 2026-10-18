from answer_check import find_mistake, is_correct, looks_like_answer, wrong_answer_feedback
from content_provider import MockContentProvider
from intent_gate import detect_intent
from leak_check import safe_hint
import sys

from model_provider import MockModelProvider, OCIModelProvider


GREETING = ("Hi! I'm an AI tutor, not a person. I'll give you hints, not answers. "
            "Your teacher can see how far you got, but not what you type.")

SAFETY_SCRIPT = ("Thank you for telling me. That sounds really important, and you are not in trouble. "
                 "Please tell your teacher or another grown-up you trust right now. "
                 "I'm letting a grown-up at your school know so they can help.")


class TutorSession:
    """One student working on one problem. handle() returns a dict so a web API can reuse it."""

    def __init__(self, content=None, model=None):
        self.content = content or MockContentProvider()
        self.model = model or MockModelProvider()
        self.solved = False
        self.events = []  # for the teacher view: (event, rung)

    def _log(self, event):
        self.events.append({"event": event, "rung": self.content.rung})

    def _hint(self, approved, rung):
        try:
            candidate = self.model.generate_hint(approved, rung)
        except Exception:
            candidate = None  # model failed: fall back to the approved hint
        return safe_hint(candidate, approved, self.content.forbidden_answers())

    def handle(self, message):
        if detect_intent(message) == "safety_concern":
            # Fixed script, never generated. The disclosure goes to one trusted adult.
            self._log("safety_concern")
            return self._reply(SAFETY_SCRIPT, "safety_concern")

        if is_correct(message, self.content.accepted_answers()):
            self.solved = True
            self._log("solved")
            return self._reply("Yes! You got it, and you worked it out yourself. Great thinking!", "solved")

        intent = detect_intent(message)

        if intent == "general_question" and looks_like_answer(message):
            self._log("wrong_answer")
            feedback = find_mistake(message, self.content.common_mistakes())
            if not feedback:
                feedback = wrong_answer_feedback(
                    message, self.content.addends(), self.content.correct_value())
            return self._reply(feedback, "ok")

        self._log(intent)

        if intent == "quit":
            return self._reply("Great work practicing today!", "ended")
        if intent == "empty":
            return self._reply("Type your answer, or ask for a hint.", "ok")
        if intent == "bypass_attempt":
            return self._reply("Let's keep learning fractions together. Want a hint?", "ok")
        if intent == "answer_request":
            current = self.content.current_hint()
            if current:
                return self._reply("I can't give you the answer, but you can do this! Remember: " + current, "ok")
            return self._reply("I can't give you the answer, but I can help you find it. Type 'hint'.", "ok")
        if intent == "hint_request":
            if self.content.rung == 3:
                self.events[-1]["event"] = "needs_teacher"
                return self._reply(
                    "That was my last hint! You have all the pieces now. Give it a try, "
                    "or raise your hand and ask your teacher. They'd love to help!", "needs_teacher")
            rung, approved = self.content.next_hint()
            return self._reply(self._hint(approved, rung), "ok")
        try:
            candidate = self.model.explain(message, self.content.question())
        except Exception:
            candidate = None
        fallback = "Good question! Try an answer, or type 'hint' if you're stuck."
        return self._reply(safe_hint(candidate, fallback, self.content.forbidden_answers()), "ok")

    def _reply(self, text, status):
        return {"reply": text, "rung": self.content.rung, "status": status}


def main():
    model = None
    if "--live" in sys.argv:
        model = OCIModelProvider()
        print("(Live mode: replies are written by OCI Generative AI.)")
    session = TutorSession(model=model)
    print("Welcome to LearnWise AI!")
    print(GREETING)
    print("Question:", session.content.question())
    print("Type your answer, 'hint' for help, or 'quit' to exit.")

    while True:
        result = session.handle(input("\nStudent: "))
        print("AI Tutor:", result["reply"])
        if result["status"] in ("ended", "solved"):
            break


if __name__ == "__main__":
    main()
