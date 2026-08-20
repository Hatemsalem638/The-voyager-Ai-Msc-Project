type VoiceControlsProps = {
  speechInputSupported: boolean;
  speechOutputSupported: boolean;
  isListening: boolean;
  isSpeaking: boolean;
  isProcessing: boolean;
  isCheckingMicrophone: boolean;
  hasMicrophone: boolean | null;
  voiceConversationActive: boolean;
  voiceModeEnabled: boolean;
  statusText: string;
  onStartListening: () => void;
  onStop: () => void;
  onToggleVoiceMode: () => void;
};

export function VoiceControls({
  speechInputSupported,
  speechOutputSupported,
  isListening,
  isSpeaking,
  isProcessing,
  isCheckingMicrophone,
  hasMicrophone,
  voiceConversationActive,
  voiceModeEnabled,
  statusText,
  onStartListening,
  onStop,
  onToggleVoiceMode,
}: VoiceControlsProps) {
  const voiceInputUnavailable = !speechInputSupported || hasMicrophone === false;
  const voiceState = isListening
    ? "Listening..."
    : isProcessing
      ? "Thinking..."
      : isCheckingMicrophone
        ? "Checking..."
      : isSpeaking
        ? "Speaking..."
        : "Start voice";

  return (
    <div className="voice-controls" aria-label="Voice controls">
      <div className="voice-actions">
        <button
          aria-label={isListening ? "Listening" : "Start voice input"}
          className={`voice-button voice-button-mic${isListening ? " voice-button-active" : ""}`}
          disabled={voiceInputUnavailable || isProcessing || isCheckingMicrophone || isSpeaking}
          onClick={onStartListening}
          type="button"
        >
          <span aria-hidden="true" className="voice-dot" />
          {voiceState}
        </button>
        <button
          aria-label="Stop listening or speaking"
          className="voice-button voice-button-stop"
          disabled={!isListening && !isSpeaking && !voiceConversationActive}
          onClick={onStop}
          type="button"
        >
          Stop
        </button>
        <label
          className={`voice-toggle${!speechOutputSupported ? " voice-toggle-disabled" : ""}`}
        >
          <input
            checked={voiceModeEnabled}
            disabled={!speechOutputSupported}
            onChange={onToggleVoiceMode}
            type="checkbox"
          />
          <span>Voice mode</span>
        </label>
      </div>
      {statusText && <p className="voice-status">{statusText}</p>}
    </div>
  );
}
