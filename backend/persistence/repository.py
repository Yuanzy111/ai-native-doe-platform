"""SQLite persistence with explicit transaction boundaries (architecture v0.2, §7).

A single-file SQLite store whose tables map one-to-one onto the §2 domain
models. Each row keeps the full Pydantic model as a ``camelCase`` JSON blob in a
``data`` column plus a few extracted columns for keys and indexing, so the
persisted shape matches §8 exactly and querying stays cheap.

Immutability is enforced at the boundary: revisions, measurements, and decision
logs expose only append/read operations (no update/delete), and the revision
chain is checked for monotonic ``revisionNumber`` and correct parent linkage on
insert. Multi-step orchestration (§4.1) runs inside :meth:`SqliteRepository.transaction`,
which is reentrant so an application-service unit of work composes cleanly.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from backend.domain.models import (
    CampaignDefinition,
    CampaignDefinitionRevision,
    CampaignRun,
    DecisionLog,
    ExperimentRound,
    ExperimentRun,
    Measurement,
    RecommendationBatch,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign_definition (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    head_revision_id TEXT NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_definition_revision (
    id TEXT PRIMARY KEY,
    campaign_definition_id TEXT NOT NULL REFERENCES campaign_definition (id),
    revision_number INTEGER NOT NULL,
    data TEXT NOT NULL,
    UNIQUE (campaign_definition_id, revision_number)
);
CREATE INDEX IF NOT EXISTS ix_revision_definition
    ON campaign_definition_revision (campaign_definition_id);

CREATE TABLE IF NOT EXISTS campaign_run (
    id TEXT PRIMARY KEY,
    campaign_definition_id TEXT NOT NULL REFERENCES campaign_definition (id),
    definition_revision_id TEXT NOT NULL REFERENCES campaign_definition_revision (id),
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_run_definition
    ON campaign_run (campaign_definition_id);

CREATE TABLE IF NOT EXISTS experiment_round (
    id TEXT PRIMARY KEY,
    campaign_run_id TEXT NOT NULL REFERENCES campaign_run (id),
    round_number INTEGER NOT NULL,
    data TEXT NOT NULL,
    UNIQUE (campaign_run_id, round_number)
);
CREATE INDEX IF NOT EXISTS ix_round_run
    ON experiment_round (campaign_run_id);

CREATE TABLE IF NOT EXISTS experiment_run (
    id TEXT PRIMARY KEY,
    campaign_run_id TEXT NOT NULL REFERENCES campaign_run (id),
    experiment_round_id TEXT NOT NULL REFERENCES experiment_round (id),
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_experiment_run_round
    ON experiment_run (experiment_round_id);
CREATE INDEX IF NOT EXISTS ix_experiment_run_run
    ON experiment_run (campaign_run_id);

CREATE TABLE IF NOT EXISTS measurement (
    id TEXT PRIMARY KEY,
    experiment_run_id TEXT NOT NULL REFERENCES experiment_run (id),
    output_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    data TEXT NOT NULL,
    UNIQUE (experiment_run_id, output_id, revision)
);
CREATE INDEX IF NOT EXISTS ix_measurement_experiment_run
    ON measurement (experiment_run_id);

CREATE TABLE IF NOT EXISTS recommendation_batch (
    id TEXT PRIMARY KEY,
    campaign_run_id TEXT NOT NULL REFERENCES campaign_run (id),
    round_number INTEGER NOT NULL,
    data TEXT NOT NULL,
    UNIQUE (campaign_run_id, round_number)
);
CREATE INDEX IF NOT EXISTS ix_batch_run
    ON recommendation_batch (campaign_run_id);

CREATE TABLE IF NOT EXISTS decision_log (
    id TEXT PRIMARY KEY,
    campaign_run_id TEXT NOT NULL REFERENCES campaign_run (id),
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_log_run
    ON decision_log (campaign_run_id);
"""


class PersistenceError(Exception):
    """Raised when a persistence invariant is violated."""


class SqliteRepository:
    """A document-style SQLite repository for the §2 domain models."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an existing connection (schema assumed initialized).

        Args:
            connection: A SQLite connection in autocommit mode
                (``isolation_level=None``) with ``row_factory`` set.
        """
        self._conn = connection
        self._transaction_depth = 0

    @classmethod
    def connect(cls, path: str = ":memory:") -> "SqliteRepository":
        """Open a repository at ``path`` and initialize the schema.

        Args:
            path: The SQLite file path, or ``":memory:"`` for an ephemeral store.

        Returns:
            A ready-to-use :class:`SqliteRepository`.
        """
        connection = sqlite3.connect(path, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.executescript(_SCHEMA)
        return cls(connection)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    # Transactions ----------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator["SqliteRepository"]:
        """Run a reentrant unit of work; commit on success, roll back on error.

        Nested calls join the outer transaction: only the outermost context
        issues ``BEGIN``/``COMMIT``/``ROLLBACK``.

        Yields:
            This repository, for convenient chaining.

        Raises:
            Exception: Re-raises any exception from the body after rolling back.
        """
        if self._transaction_depth == 0:
            self._conn.execute("BEGIN")
        self._transaction_depth += 1
        try:
            yield self
        except Exception:
            self._transaction_depth -= 1
            if self._transaction_depth == 0:
                self._conn.execute("ROLLBACK")
            raise
        else:
            self._transaction_depth -= 1
            if self._transaction_depth == 0:
                self._conn.execute("COMMIT")

    # Low-level helpers -----------------------------------------------------

    def _insert(self, table: str, row: dict[str, object]) -> None:
        """Insert one row, translating a duplicate key into a PersistenceError."""
        columns = ", ".join(row)
        placeholders = ", ".join(["?"] * len(row))
        try:
            self._conn.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                tuple(row.values()),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceError(
                f"Insert into {table!r} violates a uniqueness/immutability "
                f"invariant: {exc}"
            ) from exc

    def _update(self, table: str, entity_id: str, row: dict[str, object]) -> None:
        """Update the columns of one existing row by id."""
        assignments = ", ".join(f"{column} = ?" for column in row)
        cursor = self._conn.execute(
            f"UPDATE {table} SET {assignments} WHERE id = ?",
            (*row.values(), entity_id),
        )
        if cursor.rowcount == 0:
            raise PersistenceError(f"No {table!r} row with id {entity_id!r} to update.")

    def _fetch_one(self, table: str, entity_id: str) -> sqlite3.Row | None:
        """Fetch one row by id, or ``None``."""
        return self._conn.execute(
            f"SELECT data FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()

    # CampaignDefinition ----------------------------------------------------

    def add_definition(self, definition: CampaignDefinition) -> None:
        """Insert a new campaign definition container."""
        self._insert(
            "campaign_definition",
            {
                "id": definition.id,
                "name": definition.name,
                "head_revision_id": definition.head_revision_id,
                "data": definition.model_dump_json(by_alias=True),
            },
        )

    def save_definition(self, definition: CampaignDefinition) -> None:
        """Update a mutable definition container (e.g. advance the head)."""
        self._update(
            "campaign_definition",
            definition.id,
            {
                "name": definition.name,
                "head_revision_id": definition.head_revision_id,
                "data": definition.model_dump_json(by_alias=True),
            },
        )

    def get_definition(self, definition_id: str) -> CampaignDefinition | None:
        """Fetch a campaign definition by id."""
        row = self._fetch_one("campaign_definition", definition_id)
        return CampaignDefinition.model_validate_json(row["data"]) if row else None

    # CampaignDefinitionRevision (immutable, append-only) -------------------

    def add_revision(self, revision: CampaignDefinitionRevision) -> None:
        """Append an immutable revision, enforcing the chain invariant (§2.2).

        Args:
            revision: The revision to append.

        Raises:
            PersistenceError: If the revision number is not the next in
                sequence, or the parent linkage does not match the current head.
        """
        existing = self.list_revisions(revision.campaign_definition_id)
        if not existing:
            if revision.revision_number != 1 or revision.parent_revision_id is not None:
                raise PersistenceError(
                    "The first revision must have revisionNumber=1 and no parent."
                )
        else:
            head = existing[-1]
            if revision.revision_number != head.revision_number + 1:
                raise PersistenceError(
                    f"revisionNumber must increment by 1: expected "
                    f"{head.revision_number + 1}, got {revision.revision_number}."
                )
            if revision.parent_revision_id != head.id:
                raise PersistenceError(
                    "parentRevisionId must reference the current head revision "
                    f"{head.id!r}, got {revision.parent_revision_id!r}."
                )
        self._insert(
            "campaign_definition_revision",
            {
                "id": revision.id,
                "campaign_definition_id": revision.campaign_definition_id,
                "revision_number": revision.revision_number,
                "data": revision.model_dump_json(by_alias=True),
            },
        )

    def get_revision(self, revision_id: str) -> CampaignDefinitionRevision | None:
        """Fetch a revision by id."""
        row = self._fetch_one("campaign_definition_revision", revision_id)
        return (
            CampaignDefinitionRevision.model_validate_json(row["data"]) if row else None
        )

    def list_revisions(
        self, definition_id: str
    ) -> list[CampaignDefinitionRevision]:
        """List a definition's revisions ordered by ascending revision number."""
        rows = self._conn.execute(
            "SELECT data FROM campaign_definition_revision "
            "WHERE campaign_definition_id = ? ORDER BY revision_number ASC",
            (definition_id,),
        ).fetchall()
        return [
            CampaignDefinitionRevision.model_validate_json(row["data"]) for row in rows
        ]

    # CampaignRun -----------------------------------------------------------

    def add_run(self, run: CampaignRun) -> None:
        """Insert a new campaign run."""
        self._insert(
            "campaign_run",
            {
                "id": run.id,
                "campaign_definition_id": run.campaign_definition_id,
                "definition_revision_id": run.definition_revision_id,
                "status": run.status.value,
                "data": run.model_dump_json(by_alias=True),
            },
        )

    def save_run(self, run: CampaignRun) -> None:
        """Persist an updated run (status/round/budget changes)."""
        self._update(
            "campaign_run",
            run.id,
            {
                "definition_revision_id": run.definition_revision_id,
                "status": run.status.value,
                "data": run.model_dump_json(by_alias=True),
            },
        )

    def get_run(self, run_id: str) -> CampaignRun | None:
        """Fetch a campaign run by id."""
        row = self._fetch_one("campaign_run", run_id)
        return CampaignRun.model_validate_json(row["data"]) if row else None

    def list_runs(self, definition_id: str) -> list[CampaignRun]:
        """List all runs for a definition."""
        rows = self._conn.execute(
            "SELECT data FROM campaign_run WHERE campaign_definition_id = ?",
            (definition_id,),
        ).fetchall()
        return [CampaignRun.model_validate_json(row["data"]) for row in rows]

    # ExperimentRound -------------------------------------------------------

    def add_round(self, experiment_round: ExperimentRound) -> None:
        """Insert a new experiment round."""
        self._insert(
            "experiment_round",
            {
                "id": experiment_round.id,
                "campaign_run_id": experiment_round.campaign_run_id,
                "round_number": experiment_round.round_number,
                "data": experiment_round.model_dump_json(by_alias=True),
            },
        )

    def save_round(self, experiment_round: ExperimentRound) -> None:
        """Persist an updated experiment round (e.g. on close)."""
        self._update(
            "experiment_round",
            experiment_round.id,
            {
                "round_number": experiment_round.round_number,
                "data": experiment_round.model_dump_json(by_alias=True),
            },
        )

    def get_round(self, round_id: str) -> ExperimentRound | None:
        """Fetch an experiment round by id."""
        row = self._fetch_one("experiment_round", round_id)
        return ExperimentRound.model_validate_json(row["data"]) if row else None

    def list_rounds(self, run_id: str) -> list[ExperimentRound]:
        """List a run's experiment rounds ordered by ascending round number."""
        rows = self._conn.execute(
            "SELECT data FROM experiment_round WHERE campaign_run_id = ? "
            "ORDER BY round_number ASC",
            (run_id,),
        ).fetchall()
        return [ExperimentRound.model_validate_json(row["data"]) for row in rows]

    # ExperimentRun ---------------------------------------------------------

    def add_experiment_run(self, experiment_run: ExperimentRun) -> None:
        """Insert a new experiment run."""
        self._insert(
            "experiment_run",
            {
                "id": experiment_run.id,
                "campaign_run_id": experiment_run.campaign_run_id,
                "experiment_round_id": experiment_run.experiment_round_id,
                "data": experiment_run.model_dump_json(by_alias=True),
            },
        )

    def save_experiment_run(self, experiment_run: ExperimentRun) -> None:
        """Persist an updated experiment run (status/result changes)."""
        self._update(
            "experiment_run",
            experiment_run.id,
            {
                "experiment_round_id": experiment_run.experiment_round_id,
                "data": experiment_run.model_dump_json(by_alias=True),
            },
        )

    def get_experiment_run(self, experiment_run_id: str) -> ExperimentRun | None:
        """Fetch an experiment run by id."""
        row = self._fetch_one("experiment_run", experiment_run_id)
        return ExperimentRun.model_validate_json(row["data"]) if row else None

    def list_experiment_runs(self, round_id: str) -> list[ExperimentRun]:
        """List the experiment runs of an experiment round."""
        rows = self._conn.execute(
            "SELECT data FROM experiment_run WHERE experiment_round_id = ?",
            (round_id,),
        ).fetchall()
        return [ExperimentRun.model_validate_json(row["data"]) for row in rows]

    def list_experiment_runs_for_run(self, run_id: str) -> list[ExperimentRun]:
        """List every experiment run of a campaign run, across all rounds."""
        rows = self._conn.execute(
            "SELECT data FROM experiment_run WHERE campaign_run_id = ?",
            (run_id,),
        ).fetchall()
        return [ExperimentRun.model_validate_json(row["data"]) for row in rows]

    # Measurement (immutable, append-only) ----------------------------------

    def add_measurement(self, measurement: Measurement) -> None:
        """Append an immutable measurement, validating the supersede chain (§2.12).

        Validation and insertion share one transaction so a concurrent reader can
        never observe a half-applied correction. A new revision must extend the
        current chain head contiguously: revision 1 with no predecessor, or
        ``head.revision + 1`` directly superseding ``head.id`` for the same
        ``(experimentRunId, outputId)``. This rejects gaps, branches, cycles, and
        cross ``(experimentRunId, outputId)`` supersede pointers.

        Args:
            measurement: The reading to append.

        Raises:
            PersistenceError: If the revision does not contiguously extend the
                existing chain, or the supersede pointer is malformed.
        """
        with self.transaction():
            chain = self._measurement_chain(
                measurement.experiment_run_id, measurement.output_id
            )
            if not chain:
                if measurement.revision != 1 or measurement.supersedes_measurement_id:
                    raise PersistenceError(
                        "The first measurement for an (experimentRunId, outputId) "
                        "must have revision=1 and no supersedesMeasurementId."
                    )
            else:
                head = chain[-1]
                if measurement.revision != head.revision + 1:
                    raise PersistenceError(
                        f"Measurement revision must increment by 1: expected "
                        f"{head.revision + 1}, got {measurement.revision}."
                    )
                if measurement.supersedes_measurement_id != head.id:
                    raise PersistenceError(
                        f"A new measurement revision must directly supersede the "
                        f"current chain head {head.id!r}, got "
                        f"{measurement.supersedes_measurement_id!r}."
                    )
            self._insert(
                "measurement",
                {
                    "id": measurement.id,
                    "experiment_run_id": measurement.experiment_run_id,
                    "output_id": measurement.output_id,
                    "revision": measurement.revision,
                    "data": measurement.model_dump_json(by_alias=True),
                },
            )

    def _measurement_chain(
        self, experiment_run_id: str, output_id: str
    ) -> list[Measurement]:
        """Return one ``(experimentRunId, outputId)`` chain ordered by revision."""
        rows = self._conn.execute(
            "SELECT data FROM measurement WHERE experiment_run_id = ? "
            "AND output_id = ? ORDER BY revision ASC",
            (experiment_run_id, output_id),
        ).fetchall()
        return [Measurement.model_validate_json(row["data"]) for row in rows]

    def get_measurement(self, measurement_id: str) -> Measurement | None:
        """Fetch a measurement by id."""
        row = self._fetch_one("measurement", measurement_id)
        return Measurement.model_validate_json(row["data"]) if row else None

    def list_measurements(self, experiment_run_id: str) -> list[Measurement]:
        """List an experiment run's measurements ordered by ascending revision."""
        rows = self._conn.execute(
            "SELECT data FROM measurement WHERE experiment_run_id = ? "
            "ORDER BY output_id ASC, revision ASC",
            (experiment_run_id,),
        ).fetchall()
        return [Measurement.model_validate_json(row["data"]) for row in rows]

    # RecommendationBatch ---------------------------------------------------

    def add_batch(self, batch: RecommendationBatch) -> None:
        """Insert a new recommendation batch."""
        self._insert(
            "recommendation_batch",
            {
                "id": batch.id,
                "campaign_run_id": batch.campaign_run_id,
                "round_number": batch.round_number,
                "data": batch.model_dump_json(by_alias=True),
            },
        )

    def save_batch(self, batch: RecommendationBatch) -> None:
        """Persist an updated batch (status changes)."""
        self._update(
            "recommendation_batch",
            batch.id,
            {
                "round_number": batch.round_number,
                "data": batch.model_dump_json(by_alias=True),
            },
        )

    def get_batch(self, batch_id: str) -> RecommendationBatch | None:
        """Fetch a recommendation batch by id."""
        row = self._fetch_one("recommendation_batch", batch_id)
        return RecommendationBatch.model_validate_json(row["data"]) if row else None

    def list_batches(self, run_id: str) -> list[RecommendationBatch]:
        """List a run's recommendation batches ordered by ascending round."""
        rows = self._conn.execute(
            "SELECT data FROM recommendation_batch WHERE campaign_run_id = ? "
            "ORDER BY round_number ASC",
            (run_id,),
        ).fetchall()
        return [RecommendationBatch.model_validate_json(row["data"]) for row in rows]

    # DecisionLog (append-only) ---------------------------------------------

    def append_decision_log(self, log: DecisionLog) -> None:
        """Append an immutable decision-log entry (§2.14)."""
        self._insert(
            "decision_log",
            {
                "id": log.id,
                "campaign_run_id": log.campaign_run_id,
                "data": log.model_dump_json(by_alias=True),
            },
        )

    def list_decision_logs(self, run_id: str) -> list[DecisionLog]:
        """List a run's decision-log entries in insertion order."""
        rows = self._conn.execute(
            "SELECT data FROM decision_log WHERE campaign_run_id = ? "
            "ORDER BY rowid ASC",
            (run_id,),
        ).fetchall()
        return [DecisionLog.model_validate_json(row["data"]) for row in rows]


__all__ = ["PersistenceError", "SqliteRepository"]
