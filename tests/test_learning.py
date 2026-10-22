"""Обратная связь по откликам: разбор подписей hh и разрезы по признакам."""

import unittest
from unittest.mock import patch

from scout import learning


class LabelTests(unittest.TestCase):
    def test_invitation_wins_over_viewed(self):
        # У приглашения карточка помечена и просмотренной тоже; порядок разбора
        # обязан отдавать приглашение, иначе лучший исход теряется.
        self.assertEqual(learning.classify_label("Просмотрен\nПриглашение\nСбер"), "invited")

    def test_interview_counts_as_invitation(self):
        self.assertEqual(learning.classify_label("Собеседование\nСбер. IT"), "invited")

    def test_rejection(self):
        self.assertEqual(learning.classify_label("Отказ\nYADRO"), "rejected")

    def test_unseen_is_silence(self):
        self.assertEqual(learning.classify_label("Не просмотрен\nРексофт"), "silent")

    def test_unknown_label_is_silence(self):
        # Незнакомая подпись не должна превращаться в ответ: это завысит долю.
        self.assertEqual(learning.classify_label("Что-то новое"), "silent")


def row(vacancy_id, response, resume="Специалист сопровождения систем", letter_len=1100):
    return {"vacancy_id": vacancy_id, "outcome": "sent", "response": response,
            "resume": resume, "letter_len": letter_len}


def card(vacancy_id, score, opinion="Рекомендую"):
    return {"id": vacancy_id, "score": score, "opinion": opinion,
            "track": "support", "employer_rating": 4.5}


class ReportTests(unittest.TestCase):
    def test_no_outcomes_says_so(self):
        with patch("scout.learning.store.sent_applications", return_value=[]):
            self.assertEqual(learning.report()["sent_with_outcome"], 0)

    def test_rejection_counts_as_an_answer(self):
        # Отказ означает, что резюме открыли: для попадания в фильтр это успех.
        rows = [row("1", "rejected"), row("2", "silent")]
        cards = [card("1", 90), card("2", 90)]
        with patch("scout.learning.store.sent_applications", return_value=rows), \
             patch("scout.learning.store.list_finds", return_value=cards):
            data = learning.report()
        self.assertEqual(data["итого"]["ответили"], 1)
        self.assertEqual(data["итого"]["доля ответов"], 50)

    def test_small_groups_are_marked_insignificant(self):
        rows = [row(str(i), "invited") for i in range(3)]
        cards = [card(str(i), 95) for i in range(3)]
        with patch("scout.learning.store.sent_applications", return_value=rows), \
             patch("scout.learning.store.list_finds", return_value=cards):
            data = learning.report()
        self.assertFalse(data["разрезы"]["балл"]["90+"]["значимо"])

    def test_advice_stays_quiet_without_significant_difference(self):
        rows = [row(str(i), "invited" if i % 2 else "silent") for i in range(4)]
        cards = [card(str(i), 95) for i in range(4)]
        with patch("scout.learning.store.sent_applications", return_value=rows), \
             patch("scout.learning.store.list_finds", return_value=cards):
            lines = learning.advice()
        self.assertTrue(any("Значимых различий нет" in line for line in lines))

    def test_advice_names_the_better_band(self):
        # Десять откликов с баллом 90+ отвечают, десять с 60-69 молчат.
        rows = [row(f"h{i}", "invited") for i in range(10)]
        rows += [row(f"l{i}", "silent") for i in range(10)]
        cards = [card(f"h{i}", 95) for i in range(10)]
        cards += [card(f"l{i}", 65) for i in range(10)]
        with patch("scout.learning.store.sent_applications", return_value=rows), \
             patch("scout.learning.store.list_finds", return_value=cards):
            lines = learning.advice()
        joined = " ".join(lines)
        self.assertIn("балл", joined)
        self.assertIn("90+", joined)

    def test_collect_without_journal_touches_no_network(self):
        with patch("scout.learning.store.sent_applications", return_value=[]):
            outcome = learning.collect()
        self.assertEqual(outcome["read"], 0)


if __name__ == "__main__":
    unittest.main()
