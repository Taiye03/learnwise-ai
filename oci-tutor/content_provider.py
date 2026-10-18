# Mock content provider: stands in for Oracle AI Database until it is ready.
# Swap for an Oracle-backed class with the same methods.

PROBLEMS = {
    "frac-add-1": {
        "question": "What is 1/2 + 1/3?",
        "hints": {
            1: "Look at the bottom numbers, 2 and 3. They are different, "
               "so we can't add the fractions yet. We need them to match first!",
            2: "Find a number that both 2 and 3 fit into. Count by 2s (2, 4, 6...) "
               "and by 3s (3, 6...). Which number shows up in both lists?",
            3: "Here's a similar one: 1/4 + 1/2. Both can be fourths, and 1/2 is the same as 2/4. "
               "So 1/4 + 2/4 = 3/4. Now try yours the same way: what can halves and thirds both become?",
        },
        # The numbers being added and the correct value, used to explain wrong answers.
        "addends": ["1/2", "1/3"],
        "correct_value": "5/6",
        # Ways a student might write the right answer.
        "accepted_answers": ["5/6", "five sixths", "10/12"],
        # Known wrong answers and the misconception behind each one.
        "common_mistakes": {
            "2/5": "It looks like you added the top numbers and the bottom numbers. "
                   "We can only add the top numbers when the bottom numbers match!",
            "2/6": "Close! You found a common bottom number, but check the top numbers. "
                   "How many sixths is 1/2? How many sixths is 1/3?",
        },
        # Forms the tutor must never say. Decimals are prefixes so 0.83333 is caught.
        "forbidden_answers": ["5/6", "five sixths", "10/12", "0.83", "0.833", "0.8333"],
    },
}


class MockContentProvider:
    def __init__(self, problem_id="frac-add-1"):
        self.problem_id = problem_id
        self.problem = PROBLEMS[problem_id]
        self.rung = 0

    def question(self):
        return self.problem["question"]

    def current_hint(self):
        if self.rung == 0:
            return None
        return self.problem["hints"][self.rung]

    def next_hint(self):
        self.rung = min(3, self.rung + 1)
        return self.rung, self.problem["hints"][self.rung]

    def accepted_answers(self):
        return self.problem["accepted_answers"]

    def addends(self):
        return self.problem["addends"]

    def correct_value(self):
        return self.problem["correct_value"]

    def common_mistakes(self):
        return self.problem["common_mistakes"]

    def forbidden_answers(self):
        return self.problem["forbidden_answers"]
