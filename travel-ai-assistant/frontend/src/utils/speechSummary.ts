import type {
  ChatResponse,
  FlightResult,
  HotelResult,
  PackageResult,
} from "../api/chatApi";

function formatMoney(
  value: number | string | null | undefined,
  currency: string | null | undefined = "GBP",
) {
  const amount = Number(value);
  if (value === null || value === undefined || Number.isNaN(amount)) {
    return "";
  }
  return new Intl.NumberFormat("en-GB", {
    style: "currency",
    currency: currency ?? "GBP",
    maximumFractionDigits: 2,
  })
    .format(amount)
    .replace("£", " pounds ")
    .replace("Â£", " pounds ");
}

function cleanForSpeech(value: string) {
  return value
    .replace(/[*_`#>-]/g, " ")
    .replace(/\bVAI-[A-Z]+-\d+\b/g, "")
    .replace(/\bOffer\s+[A-Za-z0-9]+\b/g, "offer")
    .replace(/\s+/g, " ")
    .trim();
}

function limitSpeech(value: string, limit = 700) {
  const clean = cleanForSpeech(value);
  return clean.length > limit ? `${clean.slice(0, limit).trim()}...` : clean;
}

function priceNumber(value: number | string | null | undefined) {
  const amount = Number(value);
  return Number.isNaN(amount) ? Number.POSITIVE_INFINITY : amount;
}

function topPricedFlights(flights: FlightResult[]) {
  return [...flights]
    .sort((left, right) => priceNumber(left.price) - priceNumber(right.price))
    .slice(0, 3);
}

function topPricedHotels(hotels: HotelResult[]) {
  return [...hotels]
    .sort((left, right) => priceNumber(left.price) - priceNumber(right.price))
    .slice(0, 3);
}

function topPricedPackages(packages: PackageResult[]) {
  return [...packages]
    .sort(
      (left, right) =>
        priceNumber(left.totalPrice ?? left.estimated_price) -
        priceNumber(right.totalPrice ?? right.estimated_price),
    )
    .slice(0, 3);
}

function optionLabel(index: number, explicitNumber?: number | null) {
  return `option ${explicitNumber ?? index + 1}`;
}

export function getSpeechSummary(response: ChatResponse) {
  const { results } = response;
  const replyText = response.reply || "";
  const normalizedReply = replyText.toLowerCase();

  if (results.booking) {
    return limitSpeech(
      `Booking confirmed. Your booking reference is ${results.booking.reference}. Your travel documents are ready.`,
    );
  }

  if (results.flightTicket || results.hotelVoucher) {
    const reference =
      results.flightTicket?.bookingReference ?? results.hotelVoucher?.bookingReference;
    return limitSpeech(
      `Your travel documents are ready${reference ? `. Booking reference ${reference}` : ""}.`,
    );
  }

  if (
    normalizedReply.includes("you selected") ||
    normalizedReply.includes("added package") ||
    normalizedReply.includes("added to your cart") ||
    normalizedReply.includes("would you like to confirm") ||
    normalizedReply.includes("checkout complete") ||
    normalizedReply.includes("booking confirmed")
  ) {
    return limitSpeech(replyText, 500);
  }

  if (results.cart && results.cart.items.length > 0) {
    const firstItem = results.cart.items[0];
    const total = formatMoney(results.cart.totalPrice, results.cart.currency);
    if (firstItem.itemType === "hotel") {
      const hotelName =
        firstItem.hotelOption?.hotel_name ?? firstItem.title ?? "Your hotel";
      return limitSpeech(
        `${hotelName} has been added to your cart. The total is ${
          total || "shown on screen"
        }. You can continue to checkout.`,
      );
    }
    const optionNumber =
      firstItem.packageOption?.optionNumber ??
      firstItem.packageOption?.option_number ??
      1;
    return limitSpeech(
      `Package ${optionNumber} has been added to your cart. The total is ${
        total || "shown on screen"
      }. You can proceed to checkout.`,
    );
  }

  if (results.packages.length > 0) {
    const packageParts = topPricedPackages(results.packages).map(
      (packageResult, index) => {
        const label = `package ${
          packageResult.optionNumber ?? packageResult.option_number ?? index + 1
        }`;
        const total = formatMoney(
          packageResult.totalPrice ?? packageResult.estimated_price,
          packageResult.currency,
        );
        return `${label} is ${total || "priced on screen"} including flight and hotel`;
      },
    );
    return limitSpeech(
      `I found ${results.packages.length} package option${
        results.packages.length === 1 ? "" : "s"
      }. ${packageParts.join(". ")}. You can say select package 1 or ask for something cheaper.`,
    );
  }

  if (results.flights.length > 0) {
    const flights = topPricedFlights(results.flights);
    const firstFlight = flights[0];
    const route = firstFlight ? `${firstFlight.origin} to ${firstFlight.destination}` : "";
    const flightParts = flights.map((flight, index) => {
      const airline = flight.airline ? `, ${flight.airline}` : "";
      const price = formatMoney(flight.price, flight.currency);
      return `${optionLabel(index, flight.option_number)}${airline}${
        price ? `, from ${price}` : ""
      }`;
    });
    return limitSpeech(
      `I found ${results.flights.length} flight option${
        results.flights.length === 1 ? "" : "s"
      }${route ? ` for ${route}` : ""}. ${flightParts.join(
        ". ",
      )}. You can ask for something cheaper, choose an option, or ask for a package.`,
    );
  }

  if (results.hotels.length > 0) {
    const hotels = topPricedHotels(results.hotels);
    const firstHotel = hotels[0];
    const hotelParts = hotels.map((hotel, index) => {
      const price = formatMoney(hotel.price, hotel.currency);
      return `${optionLabel(index, hotel.option_number)}, ${hotel.hotel_name}${
        price ? ` from ${price}` : ""
      }`;
    });
    return limitSpeech(
      `I found ${results.hotels.length} hotel option${
        results.hotels.length === 1 ? "" : "s"
      }${firstHotel?.location ? ` in ${firstHotel.location}` : ""}. ${hotelParts.join(
        ". ",
      )}. You can choose a hotel or ask for a package.`,
    );
  }

  if (results.destinations.length > 0) {
    const topDestinations = results.destinations
      .slice(0, 3)
      .map((destination) => `${destination.city}, ${destination.country}`);
    return limitSpeech(
      `I found ${results.destinations.length} destination idea${
        results.destinations.length === 1 ? "" : "s"
      }. The top options are ${topDestinations.join(
        ", ",
      )}. You can choose one or ask for flights.`,
    );
  }

  return limitSpeech(response.reply, 500);
}
