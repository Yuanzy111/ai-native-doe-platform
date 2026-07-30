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
    AgentMessage,
    AgentProposal,
    AgentProposalStatus,
    AgentThread,
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

CREATE TABLE IF NOT EXISTS recommendation_batch (
    id TEXT PRIMARY KEY,
    campaign_run_id TEXT NOT NULL REFERENCES campaign_run (id),
    round_number INTEGER NOT NULL,
    data TEXT NOT NULL,
    UNIQUE (campaign_run_id, round_number)
);
CREATE INDEX IF NOT EXISTS ix_batch_run
    ON recommendation_batch (campaign_run_id);

CREATE TABLE IF NOT EXISTS experiment_round (
    id TEXT PRIMARY KEY,
    campaign_run_id TEXT NOT NULL REFERENCES campaign_run (id),
    round_number INTEGER NOT NULL,
    recommendation_batch_id TEXT NOT NULL REFERENCES recommendation_batch (id),
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

CREATE TABLE IF NOT EXISTS decision_log (
    id TEXT PRIMARY KEY,
    campaign_run_id TEXT NOT NULL REFERENCES campaign_run (id),
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_log_run
    ON decision_log (campaign_run_id);

CREATE TABLE IF NOT EXISTS agent_thread (
    id TEXT PRIMARY KEY,
    campaign_run_id TEXT NOT NULL UNIQUE REFERENCES campaign_run (id),
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_message (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES agent_thread (id),
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agent_message_thread
    ON agent_message (thread_id);

CREATE TABLE IF NOT EXISTS agent_proposal (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES agent_thread (id),
    campaign_run_id TEXT NOT NULL REFERENCES campaign_run (id),
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_agent_proposal_run
    ON agent_proposal (campaign_run_id);
CREATE INDEX IF NOT EXISTS ix_agent_proposal_thread
    ON agent_proposal (thread_id);
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
    def transaction(self, immediate: bool = False) -> Iterator["SqliteRepository"]:
        """Run a reentrant unit of work; commit on success, roll back on error.

        Nested calls join the outer transaction: only the outermost context
        issues ``BEGIN``/``COMMIT``/``ROLLBACK``.

        Args:
            immediate: When the *outermost* transaction, issue ``BEGIN IMMEDIATE``
                so the write lock is taken up front. A read-then-write unit of
                work (re-read the run/proposal, then mutate) must use this so a
                concurrent writer cannot slip a change in between the read and the
                write. Ignored on a nested call, which joins the outer one.

        Yields:
            This repository, for convenient chaining.

        Raises:
            Exception: Re-raises any exception from the body after rolling back.
        """
        if self._transaction_depth == 0:
            self._conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
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
        try:
            cursor = self._conn.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?",
                (*row.values(), entity_id),
            )
        except sqlite3.IntegrityError as exc:
            raise PersistenceError(
                f"Update of {table!r} violates a referential/uniqueness "
                f"invariant: {exc}"
            ) from exc
        if cursor.rowcount == 0:
            raise PersistenceError(f"No {table!r} row with id {entity_id!r} to update.")

    def _guard_immutable(
        self, table: str, entity_id: str, expected: dict[str, object]
    ) -> None:
        """Reject a save that would change a parent/ownership/identity column.

        The extracted foreign-key columns must never drift from the JSON blob, so
        a ``save_*`` is only allowed to touch the mutable state of a row. This
        fetches the persisted values of the immutable columns and raises if the
        incoming model disagrees, rather than silently rewriting the parent.
        """
        columns = ", ".join(expected)
        row = self._conn.execute(
            f"SELECT {columns} FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone()
        if row is None:
            raise PersistenceError(
                f"No {table!r} row with id {entity_id!r} to update."
            )
        for column, value in expected.items():
            if row[column] != value:
                raise PersistenceError(
                    f"{table}.{column} is immutable and cannot be changed from "
                    f"{row[column]!r} to {value!r}."
                )

    def _guard_fields_unchanged(
        self, existing: object, incoming: object, fields: list[str]
    ) -> None:
        """Reject a save that would change any of ``fields`` on a JSON-blob model.

        The extracted foreign-key columns are protected by
        :meth:`_guard_immutable`, but a document row also carries fields that
        live only inside the JSON blob (candidates, parameter values, ...). Those
        must stay fixed once created; only a model's genuinely mutable state may
        be rewritten. This compares the persisted model with the incoming one and
        raises on the first divergence.
        """
        for field_name in fields:
            old = getattr(existing, field_name)
            new = getattr(incoming, field_name)
            if old != new:
                raise PersistenceError(
                    f"{type(incoming).__name__}.{field_name} is immutable and "
                    f"cannot be changed from {old!r} to {new!r}."
                )

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
        """Insert a new campaign run, enforcing revision/campaign ownership.

        The pinned revision must exist and belong to the run's campaign
        definition; the database foreign keys alone cannot express that the
        revision and the run agree on the owning campaign.

        Raises:
            PersistenceError: If the pinned revision is unknown or belongs to a
                different campaign definition.
        """
        revision = self.get_revision(run.definition_revision_id)
        if revision is None:
            raise PersistenceError(
                f"Run references unknown revision {run.definition_revision_id!r}."
            )
        if revision.campaign_definition_id != run.campaign_definition_id:
            raise PersistenceError(
                "The run's pinned revision must belong to the run's campaign "
                f"definition {run.campaign_definition_id!r}, but revision "
                f"{revision.id!r} belongs to {revision.campaign_definition_id!r}."
            )
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
        """Persist an updated run (status/revision/counter changes).

        ``campaignDefinitionId`` is the run's parent and is immutable; only the
        pinned revision, status, and JSON state may change. Every save re-checks
        that the pinned revision exists and belongs to the run's campaign, so a
        repin can never leave the extracted column pointing at a foreign or
        dangling revision.
        """
        self._guard_immutable(
            "campaign_run",
            run.id,
            {"campaign_definition_id": run.campaign_definition_id},
        )
        revision = self.get_revision(run.definition_revision_id)
        if revision is None:
            raise PersistenceError(
                f"Run references unknown revision {run.definition_revision_id!r}."
            )
        if revision.campaign_definition_id != run.campaign_definition_id:
            raise PersistenceError(
                "The run's pinned revision must belong to the run's campaign "
                f"definition {run.campaign_definition_id!r}, but revision "
                f"{revision.id!r} belongs to {revision.campaign_definition_id!r}."
            )
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
        """Insert a new experiment round, tying it to its recommendation batch.

        The referenced batch must exist and agree with the round on both the
        owning run and the round number, so a round can never point at a batch
        from a different run or round.

        Raises:
            PersistenceError: If the batch is unknown, or its ``campaignRunId`` /
                ``roundNumber`` disagree with the round.
        """
        batch = self.get_batch(experiment_round.recommendation_batch_id)
        if batch is None:
            raise PersistenceError(
                "Round references unknown recommendation batch "
                f"{experiment_round.recommendation_batch_id!r}."
            )
        if batch.campaign_run_id != experiment_round.campaign_run_id:
            raise PersistenceError(
                "A round and its recommendation batch must belong to the same "
                f"run: round on {experiment_round.campaign_run_id!r}, batch on "
                f"{batch.campaign_run_id!r}."
            )
        if batch.round_number != experiment_round.round_number:
            raise PersistenceError(
                "A round and its recommendation batch must share a roundNumber: "
                f"round {experiment_round.round_number}, batch "
                f"{batch.round_number}."
            )
        self._insert(
            "experiment_round",
            {
                "id": experiment_round.id,
                "campaign_run_id": experiment_round.campaign_run_id,
                "round_number": experiment_round.round_number,
                "recommendation_batch_id": experiment_round.recommendation_batch_id,
                "data": experiment_round.model_dump_json(by_alias=True),
            },
        )

    def save_round(self, experiment_round: ExperimentRound) -> None:
        """Persist an updated experiment round (e.g. on close).

        The run, round number, and originating batch are immutable; only the
        JSON state (status/closedAt) may change.
        """
        self._guard_immutable(
            "experiment_round",
            experiment_round.id,
            {
                "campaign_run_id": experiment_round.campaign_run_id,
                "round_number": experiment_round.round_number,
                "recommendation_batch_id": experiment_round.recommendation_batch_id,
            },
        )
        self._update(
            "experiment_round",
            experiment_round.id,
            {"data": experiment_round.model_dump_json(by_alias=True)},
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
        """Insert a new experiment run, enforcing round/run ownership.

        The owning round must exist and belong to the same campaign run as the
        experiment run, so an experiment can never be filed under a round from a
        different run.

        Raises:
            PersistenceError: If the round is unknown or belongs to a different
                run.
        """
        experiment_round = self.get_round(experiment_run.experiment_round_id)
        if experiment_round is None:
            raise PersistenceError(
                "Experiment run references unknown round "
                f"{experiment_run.experiment_round_id!r}."
            )
        if experiment_round.campaign_run_id != experiment_run.campaign_run_id:
            raise PersistenceError(
                "An experiment run and its round must belong to the same run: "
                f"experiment on {experiment_run.campaign_run_id!r}, round on "
                f"{experiment_round.campaign_run_id!r}."
            )
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
        """Persist an updated experiment run (status/result changes).

        The identity, owning run and round, originating candidate, and the
        assigned parameter values are all fixed at creation; only the execution
        status and its metadata (executedAt/executedBy/notes) may change.
        """
        self._guard_immutable(
            "experiment_run",
            experiment_run.id,
            {
                "campaign_run_id": experiment_run.campaign_run_id,
                "experiment_round_id": experiment_run.experiment_round_id,
            },
        )
        existing = self.get_experiment_run(experiment_run.id)
        if existing is None:
            raise PersistenceError(
                f"No 'experiment_run' row with id {experiment_run.id!r} to update."
            )
        self._guard_fields_unchanged(
            existing,
            experiment_run,
            ["recommendation_candidate_id", "parameter_values"],
        )
        self._update(
            "experiment_run",
            experiment_run.id,
            {"data": experiment_run.model_dump_json(by_alias=True)},
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
            self._require_measurement_output(measurement)
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

    def _require_measurement_output(self, measurement: Measurement) -> None:
        """Reject a measurement whose output is not in the run's pinned revision.

        A measurement is attached to an experiment run, which belongs to a
        campaign run pinned to a definition revision. The measured ``outputId``
        must be one of that revision's declared outputs; otherwise the reading
        is unattributable.

        Raises:
            PersistenceError: If the experiment run, its run, or the pinned
                revision cannot be resolved, or the output is not declared.
        """
        experiment_run = self.get_experiment_run(measurement.experiment_run_id)
        if experiment_run is None:
            raise PersistenceError(
                "Measurement references unknown experiment run "
                f"{measurement.experiment_run_id!r}."
            )
        run = self.get_run(experiment_run.campaign_run_id)
        if run is None:
            raise PersistenceError(
                "Cannot resolve the campaign run for experiment run "
                f"{experiment_run.id!r}."
            )
        revision = self.get_revision(run.definition_revision_id)
        if revision is None:
            raise PersistenceError(
                "Cannot resolve the pinned revision "
                f"{run.definition_revision_id!r} for output validation."
            )
        if measurement.output_id not in {output.id for output in revision.outputs}:
            raise PersistenceError(
                f"Measurement output {measurement.output_id!r} is not declared by "
                f"the run's pinned revision {revision.id!r}."
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
        """Persist an updated batch (status changes only).

        A batch records the exact inputs, algorithm configuration, and candidates
        produced for a round; once created, every field except ``status`` is
        immutable, so a save can only advance the execution status and never
        rewrite what was recommended.
        """
        self._guard_immutable(
            "recommendation_batch",
            batch.id,
            {
                "campaign_run_id": batch.campaign_run_id,
                "round_number": batch.round_number,
            },
        )
        existing = self.get_batch(batch.id)
        if existing is None:
            raise PersistenceError(
                f"No 'recommendation_batch' row with id {batch.id!r} to update."
            )
        self._guard_fields_unchanged(
            existing,
            batch,
            [
                "generated_at",
                "input_snapshot",
                "algorithm_config",
                "candidates",
            ],
        )
        self._update(
            "recommendation_batch",
            batch.id,
            {"data": batch.model_dump_json(by_alias=True)},
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

    # Agent (thread/message/proposal) ---------------------------------------

    def get_or_create_thread(self, thread: AgentThread) -> AgentThread:
        """Return the run's existing thread, or insert and return ``thread``.

        One thread per run (MVP): if a thread already exists for
        ``thread.campaign_run_id`` it is returned unchanged and the supplied
        candidate is discarded; otherwise the candidate is inserted.
        """
        existing = self._conn.execute(
            "SELECT data FROM agent_thread WHERE campaign_run_id = ?",
            (thread.campaign_run_id,),
        ).fetchone()
        if existing is not None:
            return AgentThread.model_validate_json(existing["data"])
        self._insert(
            "agent_thread",
            {
                "id": thread.id,
                "campaign_run_id": thread.campaign_run_id,
                "data": thread.model_dump_json(by_alias=True),
            },
        )
        return thread

    def get_thread_for_run(self, run_id: str) -> AgentThread | None:
        """Fetch a run's agent thread, or ``None`` if none exists yet."""
        row = self._conn.execute(
            "SELECT data FROM agent_thread WHERE campaign_run_id = ?",
            (run_id,),
        ).fetchone()
        return AgentThread.model_validate_json(row["data"]) if row else None

    def add_agent_message(self, message: AgentMessage) -> None:
        """Append a message to an agent thread."""
        self._insert(
            "agent_message",
            {
                "id": message.id,
                "thread_id": message.thread_id,
                "data": message.model_dump_json(by_alias=True),
            },
        )

    def list_agent_messages(self, thread_id: str) -> list[AgentMessage]:
        """List a thread's messages in insertion order."""
        rows = self._conn.execute(
            "SELECT data FROM agent_message WHERE thread_id = ? ORDER BY rowid ASC",
            (thread_id,),
        ).fetchall()
        return [AgentMessage.model_validate_json(row["data"]) for row in rows]

    def add_agent_proposal(self, proposal: AgentProposal) -> None:
        """Insert a new agent proposal (created Pending)."""
        self._insert(
            "agent_proposal",
            {
                "id": proposal.id,
                "thread_id": proposal.thread_id,
                "campaign_run_id": proposal.campaign_run_id,
                "status": proposal.status,
                "data": proposal.model_dump_json(by_alias=True),
            },
        )

    def get_agent_proposal(self, proposal_id: str) -> AgentProposal | None:
        """Fetch an agent proposal by id."""
        row = self._fetch_one("agent_proposal", proposal_id)
        return AgentProposal.model_validate_json(row["data"]) if row else None

    def resolve_proposal_if_pending(self, proposal: AgentProposal) -> bool:
        """Atomically resolve a proposal only if it is still Pending in the DB.

        A database-level compare-and-set: the ``UPDATE`` matches on both the id
        and ``status = 'Pending'``, so a proposal that a concurrent request has
        already moved to a terminal state (Approved/Rejected/Failed) is left
        untouched and this returns ``False``. This is what guarantees a proposal
        resolves — and its business action dispatches — at most once under
        concurrent approve/approve or approve/reject. It is the *only* path that
        advances a proposal out of Pending, so a terminal state can never be
        overwritten.

        The owning thread/run and the immutable creation fields are guarded, so
        a caller cannot rewrite the payload or ownership through the
        compare-and-set path either.

        Returns:
            ``True`` if this call won the race and applied the update; ``False``
            if the proposal was no longer Pending.
        """
        self._guard_immutable(
            "agent_proposal",
            proposal.id,
            {
                "thread_id": proposal.thread_id,
                "campaign_run_id": proposal.campaign_run_id,
            },
        )
        existing = self.get_agent_proposal(proposal.id)
        if existing is None:
            raise PersistenceError(
                f"No 'agent_proposal' row with id {proposal.id!r} to update."
            )
        self._guard_fields_unchanged(
            existing,
            proposal,
            ["kind", "payload", "base_revision_id", "base_run_updated_at", "created_at"],
        )
        cursor = self._conn.execute(
            "UPDATE agent_proposal SET status = ?, data = ? "
            "WHERE id = ? AND status = ?",
            (
                proposal.status,
                proposal.model_dump_json(by_alias=True),
                proposal.id,
                AgentProposalStatus.PENDING.value,
            ),
        )
        return cursor.rowcount == 1

    def list_pending_proposals(self, run_id: str) -> list[AgentProposal]:
        """List a run's Pending proposals in insertion order."""
        rows = self._conn.execute(
            "SELECT data FROM agent_proposal WHERE campaign_run_id = ? "
            "AND status = ? ORDER BY rowid ASC",
            (run_id, AgentProposalStatus.PENDING.value),
        ).fetchall()
        return [AgentProposal.model_validate_json(row["data"]) for row in rows]


__all__ = ["PersistenceError", "SqliteRepository"]
