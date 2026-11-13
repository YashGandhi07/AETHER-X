"""create experiment schema

Tables for reproducible benchmark experiments (see aether_api/db_models.py):
environments, benchmark_runs, experiments, timing_samples, plus a deferred
constraint trigger that keeps each experiment's samples complete.

Revision ID: 0001
Revises:
Create Date: 2026-11-12 21:21:38.953835

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every experiment must have exactly measured_runs samples, indexed 0..n-1.
# The primary key makes indices unique and a CHECK makes them >= 0, so
# "count = n AND max = n - 1" is sufficient. Raised as check_violation (23514).
_SAMPLE_COUNT_FUNCTION = """
CREATE FUNCTION check_experiment_sample_count() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    affected uuid[];
    target uuid;
    expected integer;
    actual integer;
    highest integer;
BEGIN
    IF TG_TABLE_NAME = 'experiments' THEN
        affected := ARRAY[CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END];
    ELSIF TG_OP = 'INSERT' THEN
        affected := ARRAY[NEW.experiment_id];
    ELSIF TG_OP = 'DELETE' THEN
        affected := ARRAY[OLD.experiment_id];
    ELSE
        affected := ARRAY[OLD.experiment_id, NEW.experiment_id];
    END IF;

    FOREACH target IN ARRAY affected LOOP
        SELECT measured_runs INTO expected FROM experiments WHERE id = target;
        CONTINUE WHEN NOT FOUND;  -- experiment deleted: its samples went with it
        SELECT count(*), max(sample_index) INTO actual, highest
        FROM timing_samples WHERE experiment_id = target;
        IF actual <> expected OR highest IS DISTINCT FROM expected - 1 THEN
            RAISE EXCEPTION 'experiment % has % samples (indices up to %), expected % (0..%)',
                target, actual, coalesce(highest::text, 'none'), expected, expected - 1
                USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;
    RETURN NULL;
END
$$
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('environments',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('os_family', sa.Text(), nullable=False),
    sa.Column('os_version', sa.Text(), nullable=True),
    sa.Column('os_build', sa.Text(), nullable=True),
    sa.Column('kernel_release', sa.Text(), nullable=True),
    sa.Column('architecture', sa.Text(), nullable=False),
    sa.Column('process_translated', sa.Boolean(), nullable=True),
    sa.Column('cpu_model', sa.Text(), nullable=True),
    sa.Column('logical_cpus', sa.Integer(), nullable=True),
    sa.Column('physical_cpus', sa.Integer(), nullable=True),
    sa.Column('core_types', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('cache_line_bytes', sa.Integer(), nullable=True),
    sa.Column('memory_bytes', sa.BigInteger(), nullable=True),
    sa.Column('compiler_id', sa.Text(), nullable=False),
    sa.Column('compiler_version', sa.Text(), nullable=False),
    sa.Column('cxx_standard', sa.Text(), nullable=False),
    sa.Column('build_type', sa.Text(), nullable=False),
    sa.Column('cmake_cxx_flags', sa.Text(), nullable=True),
    sa.Column('assertions_enabled', sa.Boolean(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(core_types) = 'array'", name=op.f('ck_environments_core_types_is_array')),
    sa.CheckConstraint("os_family <> '' AND architecture <> '' AND compiler_id <> '' AND compiler_version <> '' AND cxx_standard <> '' AND build_type <> ''", name=op.f('ck_environments_required_text_not_empty')),
    sa.CheckConstraint('logical_cpus > 0 AND physical_cpus > 0 AND cache_line_bytes > 0 AND memory_bytes > 0', name=op.f('ck_environments_hardware_counts_positive')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_environments')),
    sa.UniqueConstraint('os_family', 'os_version', 'os_build', 'kernel_release', 'architecture', 'process_translated', 'cpu_model', 'logical_cpus', 'physical_cpus', 'core_types', 'cache_line_bytes', 'memory_bytes', 'compiler_id', 'compiler_version', 'cxx_standard', 'build_type', 'cmake_cxx_flags', 'assertions_enabled', name='uq_environments_identity', postgresql_nulls_not_distinct=True)
    )
    op.create_table('benchmark_runs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='running', nullable=False),
    sa.Column('request', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('wall_time_s', sa.Double(), nullable=True),
    sa.Column('engine_run_id', sa.UUID(), nullable=True),
    sa.Column('report_schema', sa.Text(), nullable=True),
    sa.Column('report_generated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('time_unit', sa.String(length=8), server_default='ms', nullable=False),
    sa.Column('environment_id', sa.BigInteger(), nullable=True),
    sa.Column('error_code', sa.Text(), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('running', 'succeeded', 'failed', 'timed_out')", name=op.f('ck_benchmark_runs_status')),
    sa.CheckConstraint("(status = 'running') = (finished_at IS NULL)", name=op.f('ck_benchmark_runs_finished_iff_not_running')),
    sa.CheckConstraint("(status IN ('failed', 'timed_out')) = (error_code IS NOT NULL)", name=op.f('ck_benchmark_runs_error_code_iff_failed')),
    sa.CheckConstraint("jsonb_typeof(request) = 'object'", name=op.f('ck_benchmark_runs_request_is_object')),
    sa.CheckConstraint("status <> 'succeeded' OR (engine_run_id IS NOT NULL AND report_schema IS NOT NULL AND report_generated_at IS NOT NULL AND environment_id IS NOT NULL AND wall_time_s IS NOT NULL)", name=op.f('ck_benchmark_runs_succeeded_has_report')),
    sa.CheckConstraint("time_unit = 'ms'", name=op.f('ck_benchmark_runs_time_unit_supported')),
    sa.CheckConstraint("wall_time_s IS NULL OR (wall_time_s >= 0 AND wall_time_s <> 'NaN'::float8 AND wall_time_s <> 'Infinity'::float8)", name=op.f('ck_benchmark_runs_wall_time_finite_non_negative')),
    sa.CheckConstraint('finished_at IS NULL OR finished_at >= started_at', name=op.f('ck_benchmark_runs_finished_after_started')),
    sa.ForeignKeyConstraint(['environment_id'], ['environments.id'], name=op.f('fk_benchmark_runs_environment_id_environments'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_benchmark_runs')),
    sa.UniqueConstraint('engine_run_id', name=op.f('uq_benchmark_runs_engine_run_id'))
    )
    op.create_index(op.f('ix_benchmark_runs_environment_id'), 'benchmark_runs', ['environment_id'], unique=False)
    op.create_index(op.f('ix_benchmark_runs_status_started_at'), 'benchmark_runs', ['status', 'started_at'], unique=False)
    op.create_table('experiments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('workload', sa.Text(), nullable=False),
    sa.Column('input_size', sa.BigInteger(), nullable=False),
    sa.Column('thread_count', sa.Integer(), nullable=False),
    sa.Column('measured_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('warmup_runs', sa.Integer(), nullable=False),
    sa.Column('measured_runs', sa.Integer(), nullable=False),
    sa.Column('min_warmup_ms', sa.Double(), nullable=False),
    sa.Column('min_sample_ms', sa.Double(), nullable=False),
    sa.Column('warmup_runs_executed', sa.Integer(), nullable=False),
    sa.Column('iterations_per_sample', sa.Integer(), nullable=False),
    sa.Column('min_ms', sa.Double(), nullable=False),
    sa.Column('max_ms', sa.Double(), nullable=False),
    sa.Column('mean_ms', sa.Double(), nullable=False),
    sa.Column('median_ms', sa.Double(), nullable=False),
    sa.Column('stddev_ms', sa.Double(), nullable=True),
    sa.Column('p95_ms', sa.Double(), nullable=False),
    sa.CheckConstraint("(stddev_ms IS NULL) = (measured_runs = 1) AND (stddev_ms IS NULL OR (stddev_ms >= 0 AND stddev_ms <> 'NaN'::float8 AND stddev_ms <> 'Infinity'::float8))", name=op.f('ck_experiments_stddev_valid')),
    sa.CheckConstraint("min_ms >= 0 AND min_ms <> 'NaN'::float8 AND min_ms <> 'Infinity'::float8 AND max_ms <> 'NaN'::float8 AND max_ms <> 'Infinity'::float8 AND min_ms <= median_ms AND median_ms <= max_ms AND min_ms <= mean_ms AND mean_ms <= max_ms AND min_ms <= p95_ms AND p95_ms <= max_ms", name=op.f('ck_experiments_statistics_ordered')),
    sa.CheckConstraint("min_warmup_ms >= 0 AND min_warmup_ms <> 'NaN'::float8 AND min_warmup_ms <> 'Infinity'::float8 AND min_sample_ms >= 0 AND min_sample_ms <> 'NaN'::float8 AND min_sample_ms <> 'Infinity'::float8", name=op.f('ck_experiments_config_durations_finite')),
    sa.CheckConstraint("workload ~ '^[a-z][a-z0-9_]{0,63}$'", name=op.f('ck_experiments_workload_format')),
    sa.CheckConstraint('input_size >= 0', name=op.f('ck_experiments_input_size_non_negative')),
    sa.CheckConstraint('iterations_per_sample > 0', name=op.f('ck_experiments_iterations_per_sample_positive')),
    sa.CheckConstraint('measured_runs > 0', name=op.f('ck_experiments_measured_runs_positive')),
    sa.CheckConstraint('thread_count > 0', name=op.f('ck_experiments_thread_count_positive')),
    sa.CheckConstraint('warmup_runs >= 0 AND warmup_runs_executed >= warmup_runs', name=op.f('ck_experiments_warmup_runs_valid')),
    sa.ForeignKeyConstraint(['run_id'], ['benchmark_runs.id'], name=op.f('fk_experiments_run_id_benchmark_runs'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_experiments'))
    )
    op.create_index(op.f('ix_experiments_run_id'), 'experiments', ['run_id'], unique=False)
    op.create_index(op.f('ix_experiments_workload_input_size_thread_count_measured_at'), 'experiments', ['workload', 'input_size', 'thread_count', 'measured_at'], unique=False)
    op.create_table('timing_samples',
    sa.Column('experiment_id', sa.UUID(), nullable=False),
    sa.Column('sample_index', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Double(), nullable=False),
    sa.CheckConstraint("duration_ms >= 0 AND duration_ms <> 'NaN'::float8 AND duration_ms <> 'Infinity'::float8", name=op.f('ck_timing_samples_duration_finite_non_negative')),
    sa.CheckConstraint('sample_index >= 0', name=op.f('ck_timing_samples_sample_index_non_negative')),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], name=op.f('fk_timing_samples_experiment_id_experiments'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('experiment_id', 'sample_index', name=op.f('pk_timing_samples'))
    )
    op.execute(_SAMPLE_COUNT_FUNCTION)
    for table in ("experiments", "timing_samples"):
        # DEFERRABLE INITIALLY DEFERRED: checked at COMMIT, after the
        # experiment row and all of its samples have been inserted.
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER {table}_sample_count
            AFTER INSERT OR UPDATE OR DELETE ON {table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION check_experiment_sample_count()
        """)


def downgrade() -> None:
    """Downgrade schema."""
    for table in ("timing_samples", "experiments"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_sample_count ON {table}")
    op.execute("DROP FUNCTION IF EXISTS check_experiment_sample_count()")
    op.drop_table('timing_samples')
    op.drop_index(op.f('ix_experiments_workload_input_size_thread_count_measured_at'), table_name='experiments')
    op.drop_index(op.f('ix_experiments_run_id'), table_name='experiments')
    op.drop_table('experiments')
    op.drop_index(op.f('ix_benchmark_runs_status_started_at'), table_name='benchmark_runs')
    op.drop_index(op.f('ix_benchmark_runs_environment_id'), table_name='benchmark_runs')
    op.drop_table('benchmark_runs')
    op.drop_table('environments')
