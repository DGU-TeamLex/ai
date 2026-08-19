import unittest
from pathlib import Path

from src.modeling.artifact_paths import portable_artifact_path


class ArtifactPathsTest(unittest.TestCase):
    def test_project_path_is_relative_and_posix(self):
        root = Path("C:/workspace/teamlex")
        path = root / "outputs" / "report.csv"
        self.assertEqual(portable_artifact_path(path, root), "outputs/report.csv")

    def test_external_path_does_not_expose_user_directory(self):
        root = Path("C:/workspace/teamlex")
        path = Path("D:/private/user/source.csv")
        self.assertEqual(portable_artifact_path(path, root), "external/source.csv")


if __name__ == "__main__":
    unittest.main()
