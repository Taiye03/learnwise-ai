class HintLadder:
    def __init__(self):
        self.level = 1

    def get_hint(self):
        hints = {
            1: "What do you know about adding fractions?",
            2: "Fractions need a common denominator before adding.",
            3: "For example, 1/4 + 1/2 = 3/4."
        }

        return hints[self.level]

    def next_hint(self):
        if self.level < 3:
            self.level += 1

        return self.get_hint()


tutor = HintLadder()

print("Welcome to your AI Math Tutor!")
print("Let's practice adding fractions.")
print()
print("Question: What is 1/2 + 1/3?")
print()

print("Tutor:", tutor.get_hint())

while True:
    response = input("\nStudent: ")

    if response.lower() == "quit":
        print("Tutor: Great job practicing today!")
        break

    elif response.lower() == "hint":
        print("Tutor:", tutor.next_hint())

    else:
        print("Tutor: Keep thinking! Type 'hint' if you need help.")

print(tutor.get_hint())
print(tutor.next_hint())
print(tutor.next_hint())