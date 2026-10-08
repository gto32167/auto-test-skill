/**
 * Shared case-level retry policy for Playwright/Node executors.
 * Each attempt object must contain status, observations and produced_data.
 */
async function runWithQualityRetry(caseId, runOnce, options = {}) {
  const priority = String(options.priority || 'P1').toUpperCase();
  const executionMode = String(options.executionMode || options.execution_mode || 'qualification');
  const requestedMinimum = Number(options.minimumAttempts || options.minimum_attempts || 2);
  const singleRunQualified = executionMode === 'risk_based_regression'
    && priority === 'P2'
    && requestedMinimum === 1
    && (options.singleRunQualified === true || options.single_run_qualified === true);
  const attempts = [];
  const run = async (number) => {
    try {
      const result = await runOnce(number);
      return { ...result, attempt: number };
    } catch (error) {
      return {
        attempt: number,
        status: 'blocked',
        blocker_type: '工具',
        blocker_reason: `执行器异常: ${error.message}`,
        observations: {},
        produced_data: {},
      };
    }
  };

  attempts.push(await run(1));
  if (singleRunQualified && attempts[0].status === 'passed') {
    return {
      case_id: caseId,
      status: 'passed',
      selected_attempt: 1,
      stability: 'qualified_single_run',
      retry_policy: {
        minimum_attempts: 1,
        required_attempts: 1,
        first_two: [attempts[0].status],
        third_attempt_required: false,
        single_run_exempt: true,
        full_retry_upgraded: false,
        priority,
      },
      attempts,
    };
  }

  attempts.push(await run(2));
  const firstTwo = attempts.map(item => item.status);
  const needsThird = singleRunQualified || firstTwo[0] !== 'passed' || firstTwo[1] !== 'passed';
  if (needsThird) attempts.push(await run(3));

  const statuses = attempts.map(item => item.status);
  let selected = attempts.length - 1;
  let finalStatus = attempts[selected].status;
  if (priority === 'P0' && attempts.length === 3) {
    const passCount = statuses.filter(item => item === 'passed').length;
    const failCount = statuses.filter(item => item === 'failed').length;
    finalStatus = passCount >= 2 ? 'passed' : (failCount >= 2 ? 'failed' : 'blocked');
    selected = finalStatus === 'blocked'
      ? 2
      : statuses.reduce((last, value, index) => value === finalStatus ? index : last, 0);
  }
  return {
    case_id: caseId,
    status: finalStatus,
    selected_attempt: selected + 1,
    stability: finalStatus === 'blocked' ? 'blocked' : (attempts.length === 3 ? 'unstable' : 'stable'),
    retry_policy: {
      minimum_attempts: singleRunQualified ? 1 : 2,
      required_attempts: attempts.length,
      first_two: firstTwo,
      third_attempt_required: needsThird,
      single_run_exempt: singleRunQualified,
      full_retry_upgraded: singleRunQualified && attempts.length === 3,
      priority,
    },
    attempts,
  };
}

module.exports = { runWithQualityRetry };
