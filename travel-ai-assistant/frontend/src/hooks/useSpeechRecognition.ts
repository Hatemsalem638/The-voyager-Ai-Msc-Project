import { useCallback, useEffect, useRef, useState } from "react";

type SpeechRecognitionAlternativeLike = {
  transcript: string;
  confidence?: number;
};

type SpeechRecognitionResultLike = {
  isFinal: boolean;
  length: number;
  item(index: number): SpeechRecognitionAlternativeLike;
  [index: number]: SpeechRecognitionAlternativeLike;
};

type SpeechRecognitionEventLike = Event & {
  resultIndex: number;
  results: {
    length: number;
    item(index: number): SpeechRecognitionResultLike;
    [index: number]: SpeechRecognitionResultLike;
  };
};

type SpeechRecognitionErrorEventLike = Event & {
  error?: string;
};

type BrowserSpeechRecognition = EventTarget & {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};

type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

declare global {
  interface Window {
    SpeechRecognition?: BrowserSpeechRecognitionConstructor;
    webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
  }
}

function getSpeechRecognitionConstructor() {
  if (typeof window === "undefined") {
    return undefined;
  }
  return window.SpeechRecognition ?? window.webkitSpeechRecognition;
}

function friendlySpeechError(error?: string) {
  if (error === "not-allowed" || error === "service-not-allowed") {
    return "Microphone access is blocked. Allow microphone access in your browser settings.";
  }
  if (error === "no-speech") {
    return "I did not hear anything. Try again or type your message.";
  }
  if (error === "audio-capture") {
    return "No microphone was detected. Connect or enable a microphone to use voice input.";
  }
  return "Voice input stopped unexpectedly. You can try again or type your message.";
}

const SILENCE_AUTO_SEND_MS = 2800;
const NO_SPEECH_TIMEOUT_MS = 9000;

export function useSpeechRecognition(language = "en-GB") {
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const silenceTimerRef = useRef<number | null>(null);
  const noSpeechTimerRef = useRef<number | null>(null);
  const accumulatedFinalRef = useRef("");
  const heardSpeechRef = useRef(false);
  const [isListening, setIsListening] = useState(false);
  const [isCheckingMicrophone, setIsCheckingMicrophone] = useState(false);
  const [hasMicrophone, setHasMicrophone] = useState<boolean | null>(null);
  const [transcript, setTranscript] = useState("");
  const [interimTranscript, setInterimTranscript] = useState("");
  const [finalTranscript, setFinalTranscript] = useState("");
  const [error, setError] = useState("");

  const isSupported = Boolean(getSpeechRecognitionConstructor());
  const canCheckMicrophone =
    typeof navigator !== "undefined" &&
    Boolean(navigator.mediaDevices?.enumerateDevices);

  const clearTimers = useCallback(() => {
    if (silenceTimerRef.current !== null) {
      window.clearTimeout(silenceTimerRef.current);
      silenceTimerRef.current = null;
    }
    if (noSpeechTimerRef.current !== null) {
      window.clearTimeout(noSpeechTimerRef.current);
      noSpeechTimerRef.current = null;
    }
  }, []);

  const checkMicrophone = useCallback(async () => {
    if (!canCheckMicrophone) {
      setHasMicrophone(null);
      setError("");
      return null;
    }

    setIsCheckingMicrophone(true);
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const foundMicrophone = devices.some((device) => device.kind === "audioinput");
      setHasMicrophone(foundMicrophone);
      setError(
        foundMicrophone
          ? ""
          : "No microphone was detected. Connect or enable a microphone to use voice input.",
      );
      return foundMicrophone;
    } catch (errorValue) {
      const errorName =
        errorValue instanceof DOMException ? errorValue.name.toLowerCase() : "";
      if (errorName.includes("notallowed") || errorName.includes("security")) {
        setError("Microphone access is blocked. Allow microphone access in your browser settings.");
        setHasMicrophone(null);
        return false;
      }
      setHasMicrophone(null);
      setError("");
      return null;
    } finally {
      setIsCheckingMicrophone(false);
    }
  }, [canCheckMicrophone]);

  const resetTranscript = useCallback(() => {
    accumulatedFinalRef.current = "";
    heardSpeechRef.current = false;
    clearTimers();
    setTranscript("");
    setInterimTranscript("");
    setFinalTranscript("");
  }, [clearTimers]);

  const stopListening = useCallback(() => {
    clearTimers();
    const recognition = recognitionRef.current;
    if (recognition) {
      recognition.stop();
    }
    setIsListening(false);
  }, [clearTimers]);

  const startListening = useCallback(async () => {
    const SpeechRecognitionConstructor = getSpeechRecognitionConstructor();
    if (!SpeechRecognitionConstructor) {
      setError("Voice input is not supported in this browser. You can still type your message.");
      return;
    }

    const microphoneAvailable = await checkMicrophone();
    if (microphoneAvailable === false) {
      return;
    }

    if (recognitionRef.current) {
      recognitionRef.current.abort();
      recognitionRef.current = null;
    }

    resetTranscript();
    const recognition = new SpeechRecognitionConstructor();
    recognition.lang = language;
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    noSpeechTimerRef.current = window.setTimeout(() => {
      if (!heardSpeechRef.current) {
        setError("I did not hear anything. Try again or type your message.");
        recognition.stop();
        setIsListening(false);
      }
    }, NO_SPEECH_TIMEOUT_MS);

    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index] ?? event.results.item(index);
        const alternative = result[0] ?? result.item(0);
        const text = alternative?.transcript ?? "";
        if (result.isFinal) {
          finalText += text;
        } else {
          interimText += text;
        }
      }

      if (interimText.trim()) {
        heardSpeechRef.current = true;
        setInterimTranscript(interimText.trim());
      }
      if (finalText.trim()) {
        heardSpeechRef.current = true;
        accumulatedFinalRef.current = `${accumulatedFinalRef.current} ${finalText.trim()}`
          .replace(/\s+/g, " ")
          .trim();
        setTranscript(accumulatedFinalRef.current);
        setInterimTranscript("");
      }

      if (heardSpeechRef.current) {
        if (noSpeechTimerRef.current !== null) {
          window.clearTimeout(noSpeechTimerRef.current);
          noSpeechTimerRef.current = null;
        }
        if (silenceTimerRef.current !== null) {
          window.clearTimeout(silenceTimerRef.current);
        }
        silenceTimerRef.current = window.setTimeout(() => {
          const cleanFinal = accumulatedFinalRef.current.trim();
          if (cleanFinal) {
            setFinalTranscript(cleanFinal);
          }
          recognition.stop();
        }, SILENCE_AUTO_SEND_MS);
      }
    };

    recognition.onerror = (event) => {
      clearTimers();
      setError(friendlySpeechError(event.error));
      if (event.error === "audio-capture") {
        setHasMicrophone(false);
      }
      setIsListening(false);
    };

    recognition.onend = () => {
      clearTimers();
      setIsListening(false);
      recognitionRef.current = null;
    };

    recognitionRef.current = recognition;
    try {
      recognition.start();
      setIsListening(true);
    } catch {
      clearTimers();
      setError("Voice input could not start. You can still type your message.");
      recognitionRef.current = null;
      setIsListening(false);
    }
  }, [checkMicrophone, clearTimers, language, resetTranscript]);

  useEffect(() => {
    void checkMicrophone();
  }, [checkMicrophone]);

  useEffect(() => {
    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices?.addEventListener
    ) {
      return undefined;
    }

    const handleDeviceChange = () => {
      void checkMicrophone();
    };
    navigator.mediaDevices.addEventListener("devicechange", handleDeviceChange);
    return () => {
      navigator.mediaDevices.removeEventListener("devicechange", handleDeviceChange);
    };
  }, [checkMicrophone]);

  useEffect(() => {
    return () => {
      clearTimers();
      if (recognitionRef.current) {
        recognitionRef.current.abort();
      }
    };
  }, [clearTimers]);

  return {
    isSupported,
    isListening,
    isCheckingMicrophone,
    canCheckMicrophone,
    hasMicrophone,
    transcript,
    interimTranscript,
    finalTranscript,
    error,
    startListening,
    stopListening,
    resetTranscript,
    checkMicrophone,
  };
}
