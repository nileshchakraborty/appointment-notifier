Feature: Appointment notifier message intelligence

  Scenario: Bulk release is separated from individual availability
    Given the source contains a bulk release and an individual availability post
    When the trend report is generated
    Then bulk releases and individual reports have separate counts

  Scenario: Invalid portal screenshots do not alert
    Given OCR identifies a calendar with no time rows and a disabled Submit button
    When the screenshot is classified
    Then the portal state is ghost_or_unbookable
    And no availability alert is produced

  Scenario: OCR results are cached by media hash
    Given the same screenshot is processed twice
    When the second screenshot is classified
    Then the cached OCR result is reused

  Scenario: Asking for the last bulk appointment returns a response
    Given the notifier has a trusted trend report and a local assistant
    When the user asks "when was the last bulk appointment"
    Then the assistant returns a response with the provider name
    And the chat exchange is persisted

  Scenario: Startup survives legacy observations
    Given an old observation has no OCR fields
    When the store records a new observation
    Then the observation is saved without crashing
