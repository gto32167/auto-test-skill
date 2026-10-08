# PRD Analysis

## Input Checklist

- release_name:
- business_context:
- source_documents:
- environment_info:
- roles_accounts:
- known_constraints:

## Information Gaps

- gap_id:
  description:
  impact:
  need_confirmation:

## Structured Decomposition

### Release Goal

- release_id:
- release_goal:
- scope_in:
- scope_out:

### Roles

- role_id:
  role_name:
  permissions:
  entry_points:

### Modules

- module_id:
  module_name:
  business_value:
  related_roles:

### Flows

- flow_id:
  trigger:
  main_path:
  alternate_paths:
  end_state:

### Field Rules

- field_id:
  field_name:
  required:
  type:
  source:
  validation_rule:
  default_value:
  editable_when:

### State Transitions

- entity_name:
  initial_state:
  intermediate_states:
  final_states:
  transition_conditions:
  forbidden_transitions:

### Exceptions And Constraints

- item_id:
  error_condition:
  expected_feedback:
  rollback_rule:
  compatibility_constraint:

### Dependencies And Risks

- risk_id:
  external_dependency:
  mock_needed:
  testability_risk:
  mitigation:

## Ambiguities

- ambiguity_id:
  source_text:
  possible_interpretations:
  test_impact:
  assumed_option:
  need_confirmation:
