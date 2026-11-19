import unittest

from tools.repo import batch_workers


class WorkersTests(unittest.TestCase):
    def test_a_batch_whose_workers_all_finish_ends_quietly(self):
        said = []
        workers = batch_workers.Workers(say=said.append)
        done = []
        for n in range(3):
            workers.add(done.append, n).start()
        workers.join()
        workers.end()
        self.assertEqual((sorted(done), said), ([0, 1, 2], []))

    def test_a_worker_that_dies_stops_the_others_and_the_batch_exits_non_zero(self):
        said = []
        workers = batch_workers.Workers(say=said.append)
        told = []

        def dies():
            raise OSError(28, "No space left on device")

        def works():   # between scenarios: told to take no more
            told.append(workers.stopping.wait(5) and not workers.going())
        workers.add(works).start()
        workers.add(dies).start()
        workers.join()
        self.assertEqual(told, [True])
        self.assertEqual(len(said), 1)
        self.assertIn("a worker died: OSError(28", said[0])
        with self.assertRaises(SystemExit) as stopped:
            workers.end()
        self.assertIn("No space left on device", str(stopped.exception.code))

    def test_only_the_first_reason_is_said(self):
        said = []
        workers = batch_workers.Workers(say=said.append)
        workers.stop("disk full")
        workers.stop("another")
        self.assertEqual(said, ["batch: stopping - disk full"])
        self.assertFalse(workers.going())


if __name__ == "__main__":
    unittest.main()
