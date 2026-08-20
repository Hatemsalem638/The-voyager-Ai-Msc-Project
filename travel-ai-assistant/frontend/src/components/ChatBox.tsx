import {
  type FormEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";

import {
  type BookingResult,
  type ChatResponse,
  type DestinationRecommendation,
  type FlightTicket,
  type HotelVoucher,
  type PackageCartSummary,
  type PackageResult,
  clearChatSessionId,
  getChatSessionId,
  sendChatMessage,
} from "../api/chatApi";
import { saveCurrentLocation } from "../api/locationApi";
import { useCart } from "../context/CartContext";
import { useSpeechRecognition } from "../hooks/useSpeechRecognition";
import { useTextToSpeech } from "../hooks/useTextToSpeech";
import { getSpeechSummary } from "../utils/speechSummary";
import { FlightCard } from "./FlightCard";
import { HotelCard } from "./HotelCard";
import { VoiceControls } from "./VoiceControls";

type Message = {
  id: number;
  sender: "user" | "assistant";
  text: string;
  response?: ChatResponse;
};

const WELCOME_MESSAGE =
  "Hello. I'm The Voyager. I can search flights and hotels, compare options, check availability, and prepare bookings for confirmation.";

function chatMessagesStorageKey(sessionId: number | null = getChatSessionId()) {
  return `voyage_chat_messages_${sessionId ?? "guest"}`;
}

function createWelcomeMessages(): Message[] {
  return [
    {
      id: 1,
      sender: "assistant",
      text: WELCOME_MESSAGE,
    },
  ];
}

function readStoredMessages(): Message[] {
  try {
    const value = localStorage.getItem(chatMessagesStorageKey());
    const parsed: unknown = value ? JSON.parse(value) : null;
    if (!Array.isArray(parsed) || parsed.length === 0) {
      return createWelcomeMessages();
    }
    return parsed.filter(
      (message): message is Message =>
        typeof message === "object" &&
        message !== null &&
        typeof (message as Message).id === "number" &&
        ((message as Message).sender === "user" || (message as Message).sender === "assistant") &&
        typeof (message as Message).text === "string",
    );
  } catch {
    return createWelcomeMessages();
  }
}

function formatMoney(
  value: number | string | null | undefined,
  currency: string | null | undefined = "USD",
) {
  if (value === null || value === undefined) {
    return "Not available";
  }

  const amount = Number(value);
  if (Number.isNaN(amount)) {
    return currency ? `${value} ${currency}` : String(value);
  }

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: currency ?? "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(amount);
}

function formatDisplayDate(value: string | null | undefined) {
  if (!value) {
    return "Not available";
  }

  const parsed = new Date(`${value}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(parsed);
}

function DetailRow({
  label,
  value,
}: {
  label: string;
  value: string | number | null | undefined;
}) {
  return (
    <div className="result-detail">
      <dt>{label}</dt>
      <dd>{value ?? "Not available"}</dd>
    </div>
  );
}

function travelDocumentNumber(value: string | null | undefined) {
  if (!value) {
    return "Not available";
  }
  return value
    .replace(`S${"IM"}-FLT-`, "VAI-FLT-")
    .replace(`S${"IM"}-HTL-`, "VAI-HTL-");
}

function PackageCard({ packageResult }: { packageResult: PackageResult }) {
  const optionNumber = packageResult.optionNumber ?? packageResult.option_number;
  const optionLabel = optionNumber
    ? `Package ${optionNumber}`
    : "Package";
  const flight = packageResult.flight;
  const hotel = packageResult.hotel;
  const total = packageResult.totalPrice ?? packageResult.estimated_price;
  const destination =
    packageResult.destinationCity ??
    packageResult.destination ??
    "Selected destination";
  const dateLabel = flight
    ? `${formatDisplayDate(flight.departureDate)} to ${formatDisplayDate(flight.returnDate)}`
    : "Dates unavailable";
  const flightRoute = flight
    ? `${flight.origin} to ${flight.destination}`
    : packageResult.flight_id ?? "Flight unavailable";
  const hotelStay = hotel
    ? `${formatDisplayDate(hotel.checkInDate)} to ${formatDisplayDate(hotel.checkOutDate)}`
    : "Stay unavailable";

  return (
    <div className="result-card result-card-package">
      <div className="package-card-hero">
        <div>
          <span className="result-type">{optionLabel}</span>
          <strong>{destination}</strong>
          <p>{dateLabel}</p>
        </div>
        <div className="package-price-block">
          <span>Total package</span>
          <strong>{formatMoney(total, packageResult.currency)}</strong>
        </div>
      </div>
      <div className="package-card-body">
        <section className="package-mini-panel">
          <span className="package-mini-icon">FLT</span>
          <div>
            <h4>{flightRoute}</h4>
            <p>{flight?.airline ?? packageResult.flight_airline ?? "Airline unavailable"}</p>
          </div>
          <strong>{flight ? formatMoney(flight.price, flight.currency) : "Not available"}</strong>
        </section>
        <section className="package-mini-panel">
          <span className="package-mini-icon package-mini-icon-hotel">HTL</span>
          <div>
            <h4>{hotel?.hotelName ?? packageResult.hotel_name ?? "Hotel unavailable"}</h4>
            <p>{hotelStay}</p>
          </div>
          <strong>{hotel ? formatMoney(hotel.price, hotel.currency) : "Not available"}</strong>
        </section>
      </div>
      <dl className="result-details package-summary-details">
        <DetailRow label="Nights" value={packageResult.nights} />
        <DetailRow label="Passengers" value={packageResult.passengers} />
        <DetailRow label="Rooms" value={packageResult.rooms} />
        <DetailRow label="Currency" value={packageResult.currency} />
      </dl>
      {packageResult.reasons && packageResult.reasons.length > 0 && (
        <ul className="card-list">
          {packageResult.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {packageResult.warnings && packageResult.warnings.length > 0 && (
        <ul className="card-list card-list-warning">
          {packageResult.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CartSummaryCard({ cart }: { cart: PackageCartSummary }) {
  return (
    <div className="result-card result-card-cart">
      <div className="result-card-header">
        <span className="result-type">Cart</span>
        <strong>{cart.cartId}</strong>
        <span className="price-pill">{formatMoney(cart.totalPrice, cart.currency)}</span>
      </div>
      <dl className="result-details">
        <DetailRow label="Status" value={cart.status} />
        <DetailRow label="Items" value={cart.items.length} />
        <DetailRow label="Total" value={formatMoney(cart.totalPrice, cart.currency)} />
      </dl>
    </div>
  );
}

function FlightTicketCard({ ticket }: { ticket: FlightTicket }) {
  return (
    <div className="result-card result-card-ticket">
      <div className="result-card-header">
        <span className="result-type">Flight ticket</span>
        <strong>{travelDocumentNumber(ticket.ticketNumber)}</strong>
      </div>
      <dl className="result-details">
        <DetailRow label="Passenger" value={ticket.passengerName} />
        <DetailRow label="Airline" value={ticket.airline} />
        <DetailRow label="Route" value={`${ticket.origin} to ${ticket.destination}`} />
        <DetailRow label="Outbound" value={`${formatDisplayDate(ticket.departureDate)} ${ticket.departureTime ?? ""}`.trim()} />
        <DetailRow label="Return" value={ticket.returnDate ? `${formatDisplayDate(ticket.returnDate)} ${ticket.returnTime ?? ""}`.trim() : "Not available"} />
        <DetailRow label="Cabin" value={ticket.cabin} />
        <DetailRow label="Reference" value={ticket.bookingReference} />
        <DetailRow label="Status" value="Confirmed" />
      </dl>
    </div>
  );
}

function HotelVoucherCard({ voucher }: { voucher: HotelVoucher }) {
  return (
    <div className="result-card result-card-voucher">
      <div className="result-card-header">
        <span className="result-type">Hotel voucher</span>
        <strong>{travelDocumentNumber(voucher.voucherNumber)}</strong>
      </div>
      <dl className="result-details">
        <DetailRow label="Hotel" value={voucher.hotelName} />
        <DetailRow label="City" value={voucher.city} />
        <DetailRow label="Check-in" value={formatDisplayDate(voucher.checkInDate)} />
        <DetailRow label="Check-out" value={formatDisplayDate(voucher.checkOutDate)} />
        <DetailRow label="Nights" value={voucher.nights} />
        <DetailRow label="Guests" value={voucher.guests} />
        <DetailRow label="Reference" value={voucher.bookingReference} />
        <DetailRow label="Status" value="Confirmed" />
      </dl>
    </div>
  );
}

function DestinationCard({
  destination,
}: {
  destination: DestinationRecommendation;
}) {
  return (
    <div className="result-card destination-card">
      <div className="result-card-header">
        <span className="result-type">Destination</span>
        <strong>
          {destination.city}, {destination.country}
        </strong>
      </div>
      <dl className="result-details">
        <DetailRow label="Airport" value={destination.airportCode} />
        <DetailRow label="Score" value={`${destination.score}/100`} />
        <DetailRow
          label="Flight time"
          value={
            destination.estimatedFlightMinutes
              ? `${destination.estimatedFlightMinutes} min`
              : "Not available"
          }
        />
      </dl>
      {destination.matchReasons.length > 0 && (
        <ul className="card-list">
          {destination.matchReasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
      {destination.warnings.length > 0 && (
        <ul className="card-list card-list-warning">
          {destination.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      <p className="muted-card-text">{destination.evidenceSummary}</p>
      <p className="card-action-hint">
        Search flights to {destination.airportCode}
      </p>
    </div>
  );
}

function BookingCard({ booking }: { booking: BookingResult }) {
  return (
    <div className="result-card result-card-booking">
      <div className="result-card-header">
        <span className="result-type">Booking</span>
        <strong>{booking.reference}</strong>
      </div>
      <dl className="result-details">
        <DetailRow label="Status" value={booking.status} />
        <DetailRow label="Destination" value={booking.destination} />
        <DetailRow label="Summary" value={booking.summary} />
        <DetailRow
          label="Price"
          value={formatMoney(booking.estimated_price, booking.currency)}
        />
      </dl>
    </div>
  );
}

function ResultSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="result-section">
      <h2>{title}</h2>
      <div className="result-grid">{children}</div>
    </section>
  );
}

function ResultsPanel({ response }: { response: ChatResponse }) {
  const { results } = response;
  const packageOnlyMode = results.packages.length > 0;

  return (
    <div className="results-panel">
      {!packageOnlyMode && results.flights.length > 0 && (
        <ResultSection title="Flights">
          {results.flights.map((flight) => (
            <FlightCard flight={flight} key={flight.id} />
          ))}
        </ResultSection>
      )}

      {!packageOnlyMode && results.hotels.length > 0 && (
        <ResultSection title="Hotels">
          {results.hotels.map((hotel) => (
            <HotelCard hotel={hotel} key={hotel.id} />
          ))}
        </ResultSection>
      )}

      {results.destinations.length > 0 && (
        <ResultSection title="Destination ideas">
          {results.destinations.map((destination) => (
            <DestinationCard
              destination={destination}
              key={`${destination.airportCode}-${destination.score}`}
            />
          ))}
        </ResultSection>
      )}

      {results.packages.length > 0 && (
        <ResultSection title="Packages">
          {[...results.packages]
            .sort((first, second) => {
              const firstNumber = first.optionNumber ?? first.option_number ?? 0;
              const secondNumber = second.optionNumber ?? second.option_number ?? 0;
              return firstNumber - secondNumber;
            })
            .map((packageResult) => (
              <PackageCard
                key={packageResult.packageId ?? packageResult.id ?? `package-${packageResult.optionNumber ?? packageResult.option_number}`}
                packageResult={packageResult}
              />
            ))}
        </ResultSection>
      )}

      {results.cart && (
        <ResultSection title="Cart">
          <CartSummaryCard cart={results.cart} />
        </ResultSection>
      )}

      {(results.flightTicket || results.hotelVoucher) && (
        <ResultSection title="Travel documents">
          {results.flightTicket && <FlightTicketCard ticket={results.flightTicket} />}
          {results.hotelVoucher && <HotelVoucherCard voucher={results.hotelVoucher} />}
        </ResultSection>
      )}

      {results.booking && (
        <ResultSection title="Booking">
          <BookingCard booking={results.booking} />
        </ResultSection>
      )}

      {results.suggestions.length > 0 && (
        <section className="suggestions">
          <h2>Suggestions</h2>
          <ul>
            {results.suggestions.map((suggestion) => (
              <li key={suggestion}>{suggestion}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function AssistantMessage({ message }: { message: Message }) {
  return (
    <>
      <p className="message-text">{message.text}</p>
      {message.response && <ResultsPanel response={message.response} />}
    </>
  );
}

export function ChatBox({ initialPrompt = "" }: { initialPrompt?: string }) {
  const navigate = useNavigate();
  const { syncPackageCart } = useCart();
  const [messages, setMessages] = useState<Message[]>(readStoredMessages);
  const [input, setInput] = useState(initialPrompt);
  const [isLoading, setIsLoading] = useState(false);
  const [isLocating, setIsLocating] = useState(false);
  const [error, setError] = useState("");
  const [voiceModeEnabled, setVoiceModeEnabled] = useState(false);
  const [voiceConversationActive, setVoiceConversationActive] = useState(false);
  const lastVoiceTranscriptRef = useRef("");
  const lastVoiceTranscriptAtRef = useRef(0);
  const lastAssistantSpeechRef = useRef("");
  const lastSpokenAssistantMessageIdRef = useRef<number | null>(null);
  const nextMessageIdRef = useRef(Date.now());
  const voiceConversationActiveRef = useRef(false);
  const userManuallyStoppedVoiceRef = useRef(false);
  const restartListeningTimerRef = useRef<number | null>(null);
  const {
    isSupported: speechInputSupported,
    isListening,
    isCheckingMicrophone,
    hasMicrophone,
    transcript,
    interimTranscript,
    finalTranscript,
    error: speechError,
    startListening,
    stopListening,
    resetTranscript,
  } = useSpeechRecognition();
  const {
    isSupported: speechOutputSupported,
    isSpeaking,
    speak,
    stopSpeaking,
  } = useTextToSpeech();

  useEffect(() => {
    try {
      localStorage.setItem(chatMessagesStorageKey(), JSON.stringify(messages));
    } catch {
      // Keep chat usable if storage fails.
    }
  }, [messages]);

  const clearRestartTimer = useCallback(() => {
    if (restartListeningTimerRef.current !== null) {
      window.clearTimeout(restartListeningTimerRef.current);
      restartListeningTimerRef.current = null;
    }
  }, []);

  const scheduleVoiceRestart = useCallback(() => {
    clearRestartTimer();
    if (
      !voiceConversationActiveRef.current ||
      userManuallyStoppedVoiceRef.current ||
      !voiceModeEnabled ||
      !speechInputSupported ||
      hasMicrophone === false
    ) {
      return;
    }

    restartListeningTimerRef.current = window.setTimeout(() => {
      if (
        voiceConversationActiveRef.current &&
        !userManuallyStoppedVoiceRef.current &&
        !isLoading &&
        !isSpeaking
      ) {
        void startListening();
      }
    }, 500);
  }, [
    clearRestartTimer,
    hasMicrophone,
    isLoading,
    isSpeaking,
    speechInputSupported,
    startListening,
    voiceModeEnabled,
  ]);

  const nextMessageId = useCallback(() => {
    nextMessageIdRef.current += 1;
    return nextMessageIdRef.current;
  }, []);

  function normalizeSpeechForComparison(value: string) {
    return value
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function looksLikeAssistantEcho(transcriptText: string) {
    const heard = normalizeSpeechForComparison(transcriptText);
    const spoken = normalizeSpeechForComparison(lastAssistantSpeechRef.current);
    return Boolean(
      heard.length > 20 &&
        spoken.length > 20 &&
        (spoken.includes(heard) || heard.includes(spoken.slice(0, 120))),
    );
  }

  const sendMessageText = useCallback(
    async (messageText: string) => {
      const trimmedInput = messageText.trim();
      if (!trimmedInput || isLoading) {
        return;
      }

      stopListening();
      stopSpeaking();

      const userMessage: Message = {
        id: nextMessageId(),
        sender: "user",
        text: trimmedInput,
      };

      setMessages((currentMessages) => [...currentMessages, userMessage]);
      setInput("");
      setIsLoading(true);
      setError("");

      try {
        const response = await sendChatMessage(trimmedInput);
        if (response.results.cart) {
          syncPackageCart(response.results.cart);
        }
        const assistantMessage: Message = {
          id: nextMessageId(),
          sender: "assistant",
          text: response.reply,
          response,
        };

        setMessages((currentMessages) => {
          const updatedMessages = [...currentMessages, assistantMessage];
          try {
            localStorage.setItem(
              chatMessagesStorageKey(response.sessionId),
              JSON.stringify(updatedMessages),
            );
          } catch {
            // Keep live chat if storage fails.
          }
          return updatedMessages;
        });
        const redirectPath =
          response.redirect_to ??
          (response.nextAction === "navigate" ? response.nextPath : null);
        if (redirectPath) {
          navigate(redirectPath);
        }
        if (voiceModeEnabled && speechOutputSupported) {
          const speechSummary = getSpeechSummary(response);
          lastAssistantSpeechRef.current = speechSummary;
          lastSpokenAssistantMessageIdRef.current = assistantMessage.id;
          stopListening();
          if (import.meta.env.DEV) {
            console.debug(
              "Voyager voice summary",
              assistantMessage.id,
              speechSummary.slice(0, 80),
            );
          }
          if (lastSpokenAssistantMessageIdRef.current === assistantMessage.id) {
            speak(speechSummary, scheduleVoiceRestart);
          }
        } else if (voiceConversationActiveRef.current) {
          scheduleVoiceRestart();
        }
      } catch {
        setError("Could not reach the backend. Check that FastAPI is running.");
        if (voiceConversationActiveRef.current) {
          scheduleVoiceRestart();
        }
      } finally {
        setIsLoading(false);
      }
    },
    [
      isLoading,
      speak,
      speechOutputSupported,
      scheduleVoiceRestart,
      stopListening,
      stopSpeaking,
      navigate,
      nextMessageId,
      syncPackageCart,
      voiceModeEnabled,
    ],
  );

  useEffect(() => {
    if (!isListening) {
      return;
    }
    const heardText = interimTranscript || transcript;
    if (heardText) {
      setInput(heardText);
    }
  }, [interimTranscript, isListening, transcript]);

  useEffect(() => {
    const cleanTranscript = finalTranscript.trim();
    const now = Date.now();
    if (
      !cleanTranscript ||
      looksLikeAssistantEcho(cleanTranscript) ||
      (cleanTranscript === lastVoiceTranscriptRef.current &&
        now - lastVoiceTranscriptAtRef.current < 6000)
    ) {
      resetTranscript();
      return;
    }
    lastVoiceTranscriptRef.current = cleanTranscript;
    lastVoiceTranscriptAtRef.current = now;
    setInput(cleanTranscript);
    void sendMessageText(cleanTranscript);
    resetTranscript();
  }, [finalTranscript, resetTranscript, sendMessageText]);

  useEffect(() => {
    return () => {
      clearRestartTimer();
      stopListening();
      stopSpeaking();
    };
  }, [clearRestartTimer, stopListening, stopSpeaking]);

  async function handleUseLocation() {
    setError("");
    if (!("geolocation" in navigator)) {
      setError("Location is not available in this browser. You can type your origin manually.");
      return;
    }

    setIsLocating(true);
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        try {
          const response = await saveCurrentLocation(
            position.coords.latitude,
            position.coords.longitude,
          );
          const assistantMessage: Message = {
            id: nextMessageId(),
            sender: "assistant",
            text: response.message,
          };
          setMessages((currentMessages) => [
            ...currentMessages,
            assistantMessage,
          ]);
          if (voiceModeEnabled && speechOutputSupported) {
            lastAssistantSpeechRef.current = response.message;
            lastSpokenAssistantMessageIdRef.current = assistantMessage.id;
            speak(response.message, scheduleVoiceRestart);
          }
        } catch {
          setError("I could not save your location. You can type your origin manually.");
        } finally {
          setIsLocating(false);
        }
      },
      () => {
        setError("Location permission was not granted. You can type your origin manually.");
        setIsLocating(false);
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await sendMessageText(input);
  }

  function handleStartVoice() {
    setError("");
    clearRestartTimer();
    userManuallyStoppedVoiceRef.current = false;
    voiceConversationActiveRef.current = true;
    setVoiceConversationActive(true);
    if (speechOutputSupported) {
      setVoiceModeEnabled(true);
    }
    stopSpeaking();
    setInput("");
    lastVoiceTranscriptRef.current = "";
    lastVoiceTranscriptAtRef.current = 0;
    void startListening();
  }

  function handleStopVoice() {
    userManuallyStoppedVoiceRef.current = true;
    voiceConversationActiveRef.current = false;
    setVoiceConversationActive(false);
    clearRestartTimer();
    stopListening();
    stopSpeaking();
  }

  function handleToggleVoiceMode() {
    setVoiceModeEnabled((currentValue) => {
      const nextValue = !currentValue;
      if (!nextValue) {
        clearRestartTimer();
      }
      return nextValue;
    });
  }

  function handleResetChat() {
    userManuallyStoppedVoiceRef.current = true;
    voiceConversationActiveRef.current = false;
    setVoiceConversationActive(false);
    clearRestartTimer();
    stopListening();
    stopSpeaking();
    setError("");
    setInput("");
    try {
      localStorage.removeItem(chatMessagesStorageKey());
      localStorage.removeItem(chatMessagesStorageKey(null));
    } catch {
      // Reset in memory even if storage fails.
    }
    clearChatSessionId();
    setMessages(createWelcomeMessages());
  }

  const voiceStatus = speechError
    || (!speechInputSupported
      ? "Voice input is not supported in this browser. You can still type your message."
      : hasMicrophone === false
        ? "No microphone was detected. Connect or enable a microphone to use voice input."
        : isListening
          ? `Listening${interimTranscript ? `... I heard: ${interimTranscript}` : "..."}`
          : isCheckingMicrophone
            ? "Checking microphone..."
            : isLoading
              ? "The Voyager is thinking..."
              : isSpeaking
                ? "The Voyager is speaking..."
                : !speechOutputSupported
                  ? "Read-aloud is not supported in this browser."
                  : voiceConversationActive
                    ? "Listening again after The Voyager replies."
                    : voiceModeEnabled
                      ? "Voice mode is on. Assistant replies will be spoken aloud."
                      : "");

  return (
    <section className="chat-shell" aria-label="The Voyager chat">
      <div className="chat-header">
        <div className="brand-lockup">
          <div className="brand-copy">
            <h1>The Voyager</h1>
          </div>
        </div>
      </div>

      <div className="messages" aria-live="polite">
        {messages.map((message) => (
          <article
            className={`message message-${message.sender}${
              message.response ? " message-with-results" : ""
            }`}
            key={message.id}
          >
            {message.sender === "assistant" ? (
              <AssistantMessage message={message} />
            ) : (
              message.text
            )}
          </article>
        ))}
        {isLoading && (
          <article className="message message-assistant">Thinking...</article>
        )}
      </div>

      {error && <p className="error-message">{error}</p>}

      <div className="chat-utility-row">
        <button
          className="secondary-chat-button chat-reset-button"
          onClick={handleResetChat}
          type="button"
        >
          Reset chat
        </button>
        <span>Reset chat does not clear your cart.</span>
      </div>

      <VoiceControls
        isListening={isListening}
        isCheckingMicrophone={isCheckingMicrophone}
        isProcessing={isLoading}
        isSpeaking={isSpeaking}
        hasMicrophone={hasMicrophone}
        onStartListening={handleStartVoice}
        onStop={handleStopVoice}
        onToggleVoiceMode={handleToggleVoiceMode}
        speechInputSupported={speechInputSupported}
        speechOutputSupported={speechOutputSupported}
        statusText={voiceStatus}
        voiceConversationActive={voiceConversationActive}
        voiceModeEnabled={voiceModeEnabled}
      />

      <form className="chat-form" onSubmit={handleSubmit}>
        <button
          className="secondary-chat-button"
          disabled={isLoading || isLocating}
          onClick={handleUseLocation}
          title="Use browser location to set your nearest airport"
          type="button"
        >
          {isLocating ? "Locating..." : "Use my location"}
        </button>
        <label className="sr-only" htmlFor="chat-message">
          Message
        </label>
        <input
          id="chat-message"
          type="text"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={isListening ? "Listening..." : "Try: Find flights to London"}
        />
        <button disabled={isLoading || input.trim().length === 0} type="submit">
          Send
        </button>
      </form>
    </section>
  );
}
