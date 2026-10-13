from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from .model_package import LoadedModelPackage, PackageUnavailable
from .models import CommitArtifact, IssueArtifact


HTTP_PATTERN = re.compile(r"[a-zA-z]+://[^\s]*")
GIT_PATTERN = re.compile(r"[Gg]it-svn-id")
ENGLISH_ALNUM_PATTERN = re.compile(r"[^A-Z^a-z^0-9^ ]")


@dataclass
class LegacyACCSPreprocessor:
    """Deterministic deployment implementation of the original ACCS text pipeline."""

    package: LoadedModelPackage

    def __post_init__(self) -> None:
        Tokenizer, PunktParameters, PunktSentenceTokenizer, TreebankWordTokenizer, SnowballStemmer = (
            self._import_dependencies()
        )
        try:
            tokenizer_payload = json.dumps(
                self.package.tokenizer_json,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self._tokenizer = Tokenizer.from_str(tokenizer_payload)
        except Exception as exc:
            raise PackageUnavailable("The bundled ACCS tokenizer.json could not be loaded") from exc

        preprocessing = self.package.preprocessing
        try:
            installed_tokenizers = version("tokenizers")
            installed_nltk = version("nltk")
        except PackageNotFoundError as exc:
            raise PackageUnavailable("A legacy ACCS preprocessing dependency is missing") from exc
        if installed_tokenizers != preprocessing.required_tokenizers_version:
            raise PackageUnavailable(
                "The installed tokenizers version does not match preprocessing.json"
            )
        if installed_nltk != preprocessing.required_nltk_version:
            raise PackageUnavailable("The installed nltk version does not match preprocessing.json")
        vocab_ids = set(self._tokenizer.get_vocab(with_added_tokens=True).values())
        required_ids = {
            preprocessing.cls_token_id,
            preprocessing.sep_token_id,
            preprocessing.pad_token_id,
            preprocessing.unk_token_id,
        }
        if not required_ids.issubset(vocab_ids):
            raise PackageUnavailable(
                "The ACCS preprocessing special token ids are absent from tokenizer.json"
            )

        punkt_parameters = PunktParameters()
        punkt_parameters.abbrev_types.update(preprocessing.punkt_parameters.abbreviations)
        punkt_parameters.collocations.update(
            tuple(item) for item in preprocessing.punkt_parameters.collocations
        )
        punkt_parameters.sent_starters.update(
            preprocessing.punkt_parameters.sentence_starters
        )
        punkt_parameters.ortho_context.update(
            preprocessing.punkt_parameters.orthographic_context
        )
        self._sentence_tokenizer = PunktSentenceTokenizer(punkt_parameters)
        self._word_tokenizer = TreebankWordTokenizer()
        self._stemmer = SnowballStemmer("english")
        self._stopwords = set(preprocessing.stopwords)

    @staticmethod
    def _import_dependencies():
        try:
            from tokenizers import Tokenizer
        except (ImportError, OSError) as exc:
            raise PackageUnavailable(
                "The optional 'tokenizers' dependency is required for legacy ACCS preprocessing"
            ) from exc
        try:
            from nltk.stem import SnowballStemmer
            from nltk.tokenize import TreebankWordTokenizer
            from nltk.tokenize.punkt import PunktParameters, PunktSentenceTokenizer
        except (ImportError, OSError) as exc:
            raise PackageUnavailable(
                "The optional 'nltk' dependency is required for legacy ACCS preprocessing"
            ) from exc
        return (
            Tokenizer,
            PunktParameters,
            PunktSentenceTokenizer,
            TreebankWordTokenizer,
            SnowballStemmer,
        )

    def _project_key(self, issue_key: str) -> str:
        preprocessing = self.package.preprocessing
        if preprocessing.project_key_strategy == "fixed":
            return preprocessing.fixed_project_key or ""
        prefix = issue_key.split("-", 1)[0].strip()
        if not prefix or not re.fullmatch(r"[A-Za-z0-9_#]+", prefix):
            raise PackageUnavailable(
                "The Issue_KEY cannot be mapped by the issue-key-prefix preprocessing strategy"
            )
        return prefix

    def text_process(self, text: str, project_key: str) -> str:
        value = HTTP_PATTERN.sub(" ", text or "")
        if "#" in project_key:
            value = re.sub(r"#-[0-9]+", " ", value)
        else:
            patterns = [key + r"-[0-9]*" for key in project_key.split("/")]
            value = re.sub("|".join(patterns), " ", value)
        value = GIT_PATTERN.sub(" ", value)

        final: list[str] = []
        for sentence in self._sentence_tokenizer.tokenize(value):
            cleaned_sentence = ENGLISH_ALNUM_PATTERN.sub(" ", sentence)
            words = [
                word
                for subsentence in self._sentence_tokenizer.tokenize(cleaned_sentence)
                for word in self._word_tokenizer.tokenize(subsentence)
            ]
            words = [word for word in words if word.lower() not in self._stopwords]
            for word in words:
                if word in self._stopwords:
                    continue
                final.append(str(self._stemmer.stem(word)))
        return " ".join(final) if final else " "

    def tokenize(self, processed_text: str) -> list[int]:
        return list(self._tokenizer.encode(processed_text, add_special_tokens=False).ids)

    def combine_pair(self, issue_ids: list[int], artifact_ids: list[int]) -> list[int]:
        preprocessing = self.package.preprocessing
        content_limit = preprocessing.max_sequence_length - 3
        issue_tokens = list(issue_ids)
        artifact_tokens = list(artifact_ids)
        if len(issue_tokens) + len(artifact_tokens) > content_limit:
            half = content_limit / 2
            if len(issue_tokens) > half and len(artifact_tokens) > half:
                issue_tokens = issue_tokens[: int(half)]
                artifact_tokens = artifact_tokens[: content_limit - len(issue_tokens)]
            elif len(issue_tokens) > half:
                issue_tokens = issue_tokens[: content_limit - len(artifact_tokens)]
            elif len(artifact_tokens) > half:
                artifact_tokens = artifact_tokens[: content_limit - len(issue_tokens)]

        combined = [preprocessing.cls_token_id]
        combined.extend(issue_tokens)
        combined.append(preprocessing.sep_token_id)
        combined.extend(artifact_tokens)
        combined.append(preprocessing.sep_token_id)
        if len(combined) < preprocessing.max_sequence_length:
            combined.extend(
                [preprocessing.pad_token_id]
                * (preprocessing.max_sequence_length - len(combined))
            )
        if len(combined) != preprocessing.max_sequence_length:
            raise PackageUnavailable("Legacy ACCS preprocessing produced an invalid sequence length")
        return combined

    def encode_pair(
        self, issue: IssueArtifact, commit: CommitArtifact
    ) -> tuple[list[int], list[int]]:
        project_key = self._project_key(issue.issue_key)
        issue_ids = self.tokenize(self.text_process(issue.issue_text, project_key))
        commit_text_ids = self.tokenize(self.text_process(commit.commit_text, project_key))
        commit_code_ids = self.tokenize(self.text_process(commit.commit_code, project_key))
        return (
            self.combine_pair(issue_ids, commit_text_ids),
            self.combine_pair(issue_ids, commit_code_ids),
        )
