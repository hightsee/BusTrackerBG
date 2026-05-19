import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import replaceIconElement from 'lucide/dist/esm/replaceElement.js';
import ArrowLeft from 'lucide/dist/esm/icons/arrow-left.js';
import ArrowRight from 'lucide/dist/esm/icons/arrow-right.js';
import BusFront from 'lucide/dist/esm/icons/bus-front.js';
import ChevronDown from 'lucide/dist/esm/icons/chevron-down.js';
import ChevronUp from 'lucide/dist/esm/icons/chevron-up.js';
import Flag from 'lucide/dist/esm/icons/flag.js';
import House from 'lucide/dist/esm/icons/house.js';
import Key from 'lucide/dist/esm/icons/key.js';
import KeyRound from 'lucide/dist/esm/icons/key-round.js';
import LocateFixed from 'lucide/dist/esm/icons/locate-fixed.js';
import LogIn from 'lucide/dist/esm/icons/log-in.js';
import LogOut from 'lucide/dist/esm/icons/log-out.js';
import MapPin from 'lucide/dist/esm/icons/map-pin.js';
import Navigation from 'lucide/dist/esm/icons/navigation.js';
import Save from 'lucide/dist/esm/icons/save.js';
import Search from 'lucide/dist/esm/icons/search.js';
import Star from 'lucide/dist/esm/icons/star.js';
import User from 'lucide/dist/esm/icons/user.js';
import UserPlus from 'lucide/dist/esm/icons/user-plus.js';

const lucideIcons = {
  ArrowLeft,
  ArrowRight,
  BusFront,
  ChevronDown,
  ChevronUp,
  Flag,
  House,
  Key,
  KeyRound,
  LocateFixed,
  LogIn,
  LogOut,
  MapPin,
  Navigation,
  Save,
  Search,
  Star,
  User,
  UserPlus
};

function createAppIcons() {
  document.querySelectorAll('[data-lucide]').forEach((element) => {
    replaceIconElement(element, { nameAttr: 'data-lucide', icons: lucideIcons, attrs: {} });
  });
}

const API_URL = '/api';
const AUTH_TOKEN_KEY = 'token';
const DEFAULT_CENTER = { lat: 44.8176, lon: 20.4569 };
const STATION_BOUNDS = [
  [44.3691, 20.0908],
  [45.0770, 20.7277]
];
const NAV_VIEWS = new Set(['home', 'search', 'navigate', 'favorites', 'profile']);
const TRAM_LINES = new Set(['2', '3', '5', '6', '7', '9', '10', '11', '12', '13', '14']);
const NAV_LOCATION_INITIAL_LIMIT = 6;
const NAV_LOCATION_RETRY_LIMIT = 10;

const state = {
  currentView: 'home',
  previousView: 'home',
  language: localStorage.getItem('language') || 'sr',
  authMode: 'login',
  authReturnView: 'profile',
  currentStop: null,
  searchQuery: '',
  searchResults: [],
  searchResultsExpanded: false,
  searchMessage: 'Search for an address to find nearby stops.',
  searchResolvedAddress: '',
  navFromQuery: '',
  navToQuery: '',
  navFromStop: null,
  navToStop: null,
  navFromSelection: null,
  navToSelection: null,
  navFromLocation: null,
  navFromLocationCandidates: [],
  navFromLocationAllCandidates: [],
  navFromSuggestions: [],
  navToSuggestions: [],
  navFromLoading: false,
  navToLoading: false,
  navRoutes: [],
  navDepartures: [],
  navExpandedRouteKeys: new Set(),
  navExpandedSuggestionKeys: new Set(),
  navFallbackSuggestion: null,
  navMessage: 'Choose start and destination stops.',
  nearbyStops: [],
  nearbyExpanded: false,
  recentLines: [],
  recentLinesExpanded: false,
  favoriteUsage: {},
  nearbyMessage: 'Loading stops around central Belgrade...',
  nearbyCenterLabel: 'Belgrade center',
  favorites: [],
  favoritesMessage: 'Loading favorites...',
  stopMessage: 'Loading departures...',
  arrivals: [],
  selectedDepartureLines: [],
  routeDirections: [],
  routeLine: '',
  routeMessage: 'Select a line to draw its route on the map.',
  activeSheet: '',
  mapPickMode: false,
  mapPickCandidate: null,
  mapPickOptions: [],
  mapPickMessage: '',
  favoriteChoiceStop: null,
  editingFavoriteName: '',
  favoriteEditMessage: '',
  markerLayer: null,
  favoriteMarkerLayer: null,
  routeLayer: null,
  locationMarker: null
};

let contentArea;
let mapContainer;
let bottomNav;
let appShell;
let map;
let navFromRequestId = 0;
let navToRequestId = 0;
let navFromInputTimer = null;
let navToInputTimer = null;
const navFromSuggestionsCache = new Map();
const navToSuggestionsCache = new Map();
const routeGeometryCache = new Map();

function loadRecentLines() {
  try {
    const parsed = JSON.parse(localStorage.getItem('recentLines') || '[]');
    if (!Array.isArray(parsed)) {
      return [];
    }

    return parsed.slice(0, 5).map((item) => {
      if (typeof item === 'string') {
        return { type: 'line', line: item };
      }

      return {
        type: item.type || 'line',
        line: item.line || '',
        stationId: item.stationId || '',
        rawStopId: item.rawStopId || '',
        name: item.name || '',
        lat: item.lat,
        lon: item.lon,
        presetLine: item.presetLine || item.line || ''
      };
    });
  } catch (error) {
    return [];
  }
}

function loadFavoriteUsage() {
  try {
    const parsed = JSON.parse(localStorage.getItem('favoriteUsage') || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (error) {
    return {};
  }
}

function favoriteUsageKey(favorite) {
  const stop = normalizeStop(favorite);
  return [
    stop.favoriteName || stop.name,
    stop.stationId,
    normalizeLineList(stop.presetLine || '')
  ].join('|');
}

function recordFavoriteUse(stop) {
  const favorite = getFavoriteForStop(stop);
  if (!favorite) {
    return;
  }

  const key = favoriteUsageKey(favorite);
  state.favoriteUsage[key] = Number(state.favoriteUsage[key] || 0) + 1;
  localStorage.setItem('favoriteUsage', JSON.stringify(state.favoriteUsage));
}

function getTopUsedFavorites(limit = 5) {
  return state.favorites
    .map((favorite, index) => ({
      favorite,
      index,
      uses: Number(state.favoriteUsage[favoriteUsageKey(favorite)] || 0)
    }))
    .sort((left, right) => right.uses - left.uses || left.index - right.index)
    .slice(0, limit)
    .map((entry) => entry.favorite);
}

function addRecentLine(line) {
  const normalizedLine = String(line || '').trim();
  if (!normalizedLine) {
    return;
  }

  state.recentLines = [
    { type: 'line', line: normalizedLine },
    ...state.recentLines.filter((item) => !(item.type === 'line' && item.line.toLowerCase() === normalizedLine.toLowerCase()))
  ].slice(0, 5);
  localStorage.setItem('recentLines', JSON.stringify(state.recentLines));
}

function addRecentStop(stop) {
  const normalizedStop = normalizeStop(stop);
  const presetLine = normalizeLineList(normalizedStop.presetLine || state.selectedDepartureLines.join(', '));
  if (!normalizedStop.stationId || !presetLine) {
    return;
  }

  const recentStop = {
    type: 'stop',
    stationId: normalizedStop.stationId,
    rawStopId: normalizedStop.rawStopId,
    name: normalizedStop.name,
    lat: normalizedStop.lat,
    lon: normalizedStop.lon,
    presetLine
  };

  state.recentLines = [
    recentStop,
    ...state.recentLines.filter((item) => !(
      item.type === 'stop'
      && item.stationId === recentStop.stationId
      && normalizeLineList(item.presetLine || '') === presetLine
    ))
  ].slice(0, 5);
  localStorage.setItem('recentLines', JSON.stringify(state.recentLines));
}

function splitLineLabel(line) {
  const value = String(line || '').trim();
  const match = value.match(/^(\d+)([a-zA-Z]*)?(.*)$/);

  if (!match) {
    return {
      numeric: false,
      number: Number.POSITIVE_INFINITY,
      suffix: value.toLowerCase(),
      rest: ''
    };
  }

  return {
    numeric: true,
    number: Number(match[1]),
    suffix: (match[2] || '').toLowerCase(),
    rest: (match[3] || '').toLowerCase()
  };
}

function compareLineLabels(a, b) {
  const left = splitLineLabel(a);
  const right = splitLineLabel(b);

  if (left.numeric && right.numeric && left.number !== right.number) {
    return left.number - right.number;
  }

  if (left.numeric !== right.numeric) {
    return left.numeric ? -1 : 1;
  }

  const suffixCompare = left.suffix.localeCompare(right.suffix, undefined, {
    numeric: true,
    sensitivity: 'base'
  });
  if (suffixCompare !== 0) {
    return suffixCompare;
  }

  const restCompare = left.rest.localeCompare(right.rest, undefined, {
    numeric: true,
    sensitivity: 'base'
  });
  if (restCompare !== 0) {
    return restCompare;
  }

  return String(a).localeCompare(String(b), undefined, {
    numeric: true,
    sensitivity: 'base'
  });
}

function parseLineList(value) {
  return String(value || '')
    .split(',')
    .map((line) => line.trim())
    .filter(Boolean)
    .sort(compareLineLabels);
}

function normalizeLineList(value) {
  return parseLineList(value).join(', ');
}

async function validateLineList(value) {
  const lines = parseLineList(value);
  if (!lines.length) {
    return true;
  }

  const checks = await Promise.all(lines.map(async (line) => {
    try {
      const data = await apiRequest(`/route?line=${encodeURIComponent(line)}`);
      return Boolean((data.directions || []).length);
    } catch (error) {
      return false;
    }
  }));

  return checks.every(Boolean);
}

const translations = {
  sr: {
    navHome: 'Pocetna',
    navSearch: 'Pretraga',
    navNavigate: 'Rute',
    navFavorites: 'Sacuvano',
    navProfile: 'Profil',
    searchInitial: 'Stanice u blizini adrese ce se pojaviti ovde.',
    nearbyLoading: 'Ucitavanje stanica oko centra Beograda...',
    loadingFavorites: 'Ucitavanje sacuvanih stanica...',
    loadingDepartures: 'Ucitavanje polazaka...',
    routeInitial: 'Izaberite liniju za prikaz trase na mapi.',
    belgradeCenter: 'Centar Beograda',
    stop: 'Stajaliste',
    logIn: 'Prijava',
    createAccount: 'Napravi nalog',
    register: 'Registracija',
    remove: 'Ukloni',
    removeFavorite: 'Ukloni iz sacuvanih',
    searchStops: 'Pretraga stanica',
    navigate: 'Navigacija',
    fromStop: 'Polaziste',
    toStop: 'Odrediste',
    routeSearchHint: 'Unesite broj stanice, naziv stanice ili adresu za polaziste i odrediste.',
    routeSearchDestinationHint: 'Direktna odredista su prikazana prva, ali mozete izabrati bilo koju stanicu.',
    useCurrentLocationStart: 'Moja lokacija',
    currentLocationStart: 'Moja lokacija',
    findRoute: 'Pronadji rutu',
    routeOptions: 'Predlozene rute',
    directRoute: 'Direktno',
    transferRoute: 'Presedanje',
    transferAt: 'Presedanje',
    walk: 'pesacenje',
    nextFromOrigin: 'Sledeci polasci sa polazista',
    nextFromTransfer: 'Sledeci polasci za presedanje',
    showOnMap: 'Prikazi na mapi',
    saveRoute: 'Sacuvaj rutu',
    routeSaved: 'Ruta je sacuvana u favorite.',
    linesAtStop: 'linija',
    chooseBothStops: 'Izaberite polaziste i odrediste.',
    resolvingStops: 'Trazenje ruta...',
    findingRoutes: 'Trazenje ruta...',
    noRoutesFound: 'Nema pronadjenih ruta za ove stanice.',
    search: 'Pretrazi',
    searchPlaceholder: 'Unesite adresu u Beogradu',
    linePlaceholder: 'Linija',
    addressSearchHint: 'Unesite adresu, ulicu ili poznato mesto. Prikazace se najblize stanice.',
    homeSearchPlaceholder: 'Pretrazite stanice po adresi',
    searchButton: 'Pretrazi po adresi',
    homeTitle: 'Mapa prvo. Polasci odmah.',
    homeIntro: 'Pronadjite stanicu, proverite linije i sacuvajte rute koje stvarno koristite kroz Beograd.',
    serviceStatus: 'GTFS + lokalna predvidjanja',
    mapCaption: 'Aktivna mapa stajalista',
    useLocation: 'Moja lokacija',
    chooseOnMap: 'Izaberi na mapi',
    nearbyStops: 'Stanice u blizini',
    refresh: 'Osvezi',
    savedStops: 'Sacuvane stanice',
    profile: 'Profil',
    signedIn: 'Prijavljeni ste',
    account: 'Nalog',
    accountCopy: 'Koristite aplikaciju bez prijave ili se prijavite za sacuvane stanice.',
    logout: 'Odjava',
    language: 'Jezik',
    serbian: 'Srpski',
    english: 'English',
    profileAccess: 'Pristup profilu',
    loginCopy: 'Prijavite se da sacuvate stanice i favorite.',
    registerCopy: 'Napravite nalog za favorite i trajno sacuvane stanice.',
    resetRequestCopy: 'Unesite korisnicko ime. Ako nalog postoji, server ce napraviti reset token.',
    resetConfirmCopy: 'Unesite reset token i novu lozinku.',
    username: 'Korisnicko ime',
    password: 'Lozinka',
    newPassword: 'Nova lozinka',
    resetToken: 'Reset token',
    forgotPassword: 'Zaboravili ste lozinku?',
    resetPassword: 'Reset lozinke',
    sendReset: 'Posalji reset',
    resetWithToken: 'Resetuj sa tokenom',
    resetSent: 'Ako nalog postoji, reset token je napravljen.',
    passwordUpdated: 'Lozinka je promenjena. Prijavite se ponovo.',
    backToLogin: 'Nazad na prijavu',
    back: 'Nazad',
    needAccount: 'Nemate nalog? Registracija',
    alreadyRegistered: 'Vec imate nalog? Prijava',
    stopNotFound: 'Stanica nije pronadjena',
    goBack: 'Nazad',
    saveFavorite: 'Sacuvaj',
    favoriteLabel: 'Naziv favorita',
    loginToSave: 'Prijavite se da sacuvate stanicu',
    upcomingDepartures: 'Sledeci polasci',
    lineRoute: 'Trasa linije',
    noLineSelected: 'Nije izabrana linija',
    showRoute: 'Trasa',
    line: 'Linija',
    noDepartures60: 'Nema zakazanih polazaka u narednih 60 minuta.',
    selectLines: 'Izaberite jednu ili vise linija. Prikazuju se polasci u narednih 60 minuta.',
    noSelectedDepartures: 'Nema polazaka za izabrane linije u narednih 60 minuta.',
    directionUnavailable: 'Smer nije dostupan',
    noFavoritesPublic: 'Za sacuvane stanice je potreban nalog, ali ostatak aplikacije radi bez prijave.',
    favoritesRequireLogin: 'Za sacuvane stanice je potrebna prijava.',
    noFavorites: 'Jos nema sacuvanih stanica.',
    enterStop: 'Unesite adresu.',
    searchingStops: 'Trazenje stanica u blizini adrese...',
    noStopMatches: 'Nema rezultata za tu pretragu.',
    addressNotFound: 'Adresa nije pronadjena.',
    resultFound: 'rezultat pronadjen.',
    resultsFound: 'rezultata pronadjeno.',
    loadingNearby: 'Ucitavanje stanica u blizini...',
    aroundLocation: 'Oko vase trenutne lokacije',
    locationFailed: 'Lokacija nije dostupna. Prikazujem centar Beograda.',
    noStopsArea: 'Nema stanica u ovoj oblasti.',
    noPredictions: 'Nema dostupnih predvidjenih polazaka.',
    stopMissing: 'Nedostaje identifikator stanice.',
    loadingRoute: 'Ucitavanje trase...',
    showingRoute: 'Prikazana je trasa',
    noRoute: 'Trasa nije vracena za ovu liniju.',
    heroEyebrow: 'Beogradski bus tracker',
    away: 'm udaljeno',
    unknown: 'Nepoznato',
    direction: 'Smer',
    stops: 'stanica',
    authenticatedUser: 'Prijavljen korisnik',
    noPlannedPrefix: 'Nema planiranih polazaka za',
    recentLines: 'Nedavno trazene linije',
    topFavorites: 'Favoriti',
    noRecentLines: 'Nema nedavno trazenih linija.',
    clearRecent: 'Ocisti',
    presetLine: 'Linija',
    openPreset: 'Polasci',
    optionalLine: 'Opciona linija',
    savePreset: 'Sacuvaj preset',
    edit: 'Izmeni',
    cancel: 'Otkazi',
    save: 'Sacuvaj',
    favoriteName: 'Naziv',
    stationNumber: 'Broj stanice',
    stationNotFound: 'Ova stanica ne postoji.',
    lineNotFound: 'Ova linija ne postoji.',
    close: 'Zatvori',
    view: 'Otvori',
    searchResultsTitle: 'Rezultati pretrage',
    chooseLines: 'Izaberite linije',
    allLines: 'Sve',
    pickMapPoint: 'Izaberi na mapi',
    pickLocationOnMap: 'Izaberi lokaciju na mapi',
    pickMapHint: 'Kliknite na mapu za stanice u blizini.',
    pickedMapPoint: 'Izabrana tacka na mapi',
    chooseNearbyStop: 'Izaberite stanicu u blizini',
    selectedStop: 'Izabrana stanica',
    registeredLines: 'Linije na stanici',
    chooseDifferent: 'Izaberi drugu',
    proceed: 'Nastavi',
    noRegisteredLines: 'Nema registrovanih linija za ovu stanicu.',
    favoriteMarker: 'Sacuvano',
    chooseStartFirst: 'Prvo izaberite polaziste.',
    destinationMatches: 'Povezana odredista',
    loadingStops: 'Ucitavanje stanica...',
    noConnectedStops: 'Nema povezanih stanica za ovu pretragu.',
    sharedLines: 'Zajednicke linije',
    directMatch: 'Direktno',
    needsTransfer: 'Potrebno presedanje',
    favoritePresetChoice: 'Izbor favorita',
    customLookup: 'Nova pretraga polazaka',
    useFavoritePreset: 'Koristi sacuvani preset'
    ,details: 'Platforme'
    ,hideDetails: 'Sakrij platforme'
    ,useSuggestedStation: 'Koristi predlog'
  },
  en: {
    navHome: 'Home',
    navSearch: 'Search',
    navNavigate: 'Routes',
    navFavorites: 'Saved',
    navProfile: 'Profile',
    searchInitial: 'Stops near the address will appear here.',
    nearbyLoading: 'Loading stops around central Belgrade...',
    loadingFavorites: 'Loading favorites...',
    loadingDepartures: 'Loading departures...',
    routeInitial: 'Select a line to draw its route on the map.',
    belgradeCenter: 'Belgrade center',
    stop: 'Stop',
    logIn: 'Log in',
    createAccount: 'Create account',
    register: 'Register',
    remove: 'Remove',
    removeFavorite: 'Remove from favorites',
    searchStops: 'Search Stops',
    navigate: 'Navigation',
    fromStop: 'Start point',
    toStop: 'End point',
    routeSearchHint: 'Enter a station number, station name, or address for the start and destination.',
    routeSearchDestinationHint: 'Direct destinations are shown first, but you can choose any stop.',
    useCurrentLocationStart: 'Use my location',
    currentLocationStart: 'My location',
    findRoute: 'Find route',
    routeOptions: 'Suggested routes',
    directRoute: 'Direct',
    transferRoute: 'Transfer',
    transferAt: 'Transfer at',
    walk: 'walk',
    nextFromOrigin: 'Next departures from start',
    nextFromTransfer: 'Next transfer departures',
    showOnMap: 'Show on map',
    saveRoute: 'Save route',
    routeSaved: 'Route saved to favorites.',
    linesAtStop: 'lines',
    chooseBothStops: 'Choose both start and destination stops.',
    resolvingStops: 'Finding routes...',
    findingRoutes: 'Finding routes...',
    noRoutesFound: 'No routes found for those stops.',
    search: 'Search',
    searchPlaceholder: 'Enter a Belgrade address',
    linePlaceholder: 'Line',
    addressSearchHint: 'Enter an address, street, or landmark. Nearby stops will be shown.',
    homeSearchPlaceholder: 'Search stops by address',
    searchButton: 'Search by address',
    homeTitle: 'Map first. Departures now.',
    homeIntro: 'Find a stop, check the lines, and keep the Belgrade routes you actually use close at hand.',
    serviceStatus: 'GTFS + local predictions',
    mapCaption: 'Live stop map',
    useLocation: 'Use my location',
    chooseOnMap: 'Choose on map',
    nearbyStops: 'Nearby Stops',
    refresh: 'Refresh',
    savedStops: 'Saved Stops',
    profile: 'Profile',
    signedIn: 'Signed in',
    account: 'Account',
    accountCopy: 'Use the app without login, or sign in for favorites.',
    logout: 'Log out',
    language: 'Language',
    serbian: 'Srpski',
    english: 'English',
    profileAccess: 'Profile access',
    loginCopy: 'Sign in to save stops and manage favorites.',
    registerCopy: 'Create an account for favorites and persistent saved stops.',
    resetRequestCopy: 'Enter your username. If the account exists, the server will create a reset token.',
    resetConfirmCopy: 'Enter the reset token and your new password.',
    username: 'Username',
    password: 'Password',
    newPassword: 'New password',
    resetToken: 'Reset token',
    forgotPassword: 'Forgot password?',
    resetPassword: 'Reset password',
    sendReset: 'Send reset',
    resetWithToken: 'Reset with token',
    resetSent: 'If that account exists, a reset token was generated.',
    passwordUpdated: 'Password updated. Log in again.',
    backToLogin: 'Back to login',
    back: 'Back',
    needAccount: 'Need an account? Register',
    alreadyRegistered: 'Already registered? Log in',
    stopNotFound: 'Stop not found',
    goBack: 'Go back',
    saveFavorite: 'Save',
    favoriteLabel: 'Favorite label',
    loginToSave: 'Log in to save this stop',
    upcomingDepartures: 'Upcoming Departures',
    lineRoute: 'Line Route',
    noLineSelected: 'No line selected',
    showRoute: 'Show route',
    line: 'Line',
    noDepartures60: 'No departures are scheduled in the next 60 minutes.',
    selectLines: 'Select one or more lines first. Only departures in the next 60 minutes will be shown.',
    noSelectedDepartures: 'No departures for the selected lines in the next 60 minutes.',
    directionUnavailable: 'Direction unavailable',
    noFavoritesPublic: 'Favorites require an account, but the rest of the app stays usable without logging in.',
    favoritesRequireLogin: 'Favorites require login.',
    noFavorites: 'No saved stops yet.',
    enterStop: 'Enter an address.',
    searchingStops: 'Finding stops near that address...',
    noStopMatches: 'No stops were found near that address.',
    addressNotFound: 'Address was not found.',
    resultFound: 'result found.',
    resultsFound: 'results found.',
    loadingNearby: 'Loading nearby stops...',
    aroundLocation: 'Around your current location',
    locationFailed: 'Location access failed. Showing central Belgrade instead.',
    noStopsArea: 'No stops were found in this area.',
    noPredictions: 'No predicted departures are available.',
    stopMissing: 'Stop identifier is missing.',
    loadingRoute: 'Loading route geometry...',
    showingRoute: 'Showing route',
    noRoute: 'No route geometry was returned for this line.',
    heroEyebrow: 'Belgrade bus tracker',
    away: 'm away',
    unknown: 'Unknown',
    direction: 'Direction',
    stops: 'stops',
    authenticatedUser: 'Authenticated user',
    noPlannedPrefix: 'No planned departures were found for',
    recentLines: 'Recently searched lines',
    topFavorites: 'Favorites',
    noRecentLines: 'No recently searched lines yet.',
    clearRecent: 'Clear',
    presetLine: 'Line',
    openPreset: 'Departures',
    optionalLine: 'Optional line',
    savePreset: 'Save preset',
    edit: 'Edit',
    cancel: 'Cancel',
    save: 'Save',
    favoriteName: 'Name',
    stationNumber: 'Station number',
    stationNotFound: 'This station does not exist.',
    lineNotFound: 'This line does not exist.',
    close: 'Close',
    view: 'View',
    searchResultsTitle: 'Search results',
    chooseLines: 'Choose lines',
    allLines: 'All',
    pickMapPoint: 'Pick on map',
    pickLocationOnMap: 'Choose a location on the map',
    pickMapHint: 'Click the map for nearby stops.',
    pickedMapPoint: 'Picked map point',
    chooseNearbyStop: 'Choose a nearby stop',
    selectedStop: 'Selected stop',
    registeredLines: 'Lines serving this stop',
    chooseDifferent: 'Choose different',
    proceed: 'Proceed',
    noRegisteredLines: 'No registered lines for this stop.',
    favoriteMarker: 'Saved',
    chooseStartFirst: 'Choose a start stop first.',
    destinationMatches: 'Connected destinations',
    loadingStops: 'Loading stops...',
    noConnectedStops: 'No connected stops match that search.',
    sharedLines: 'Shared lines',
    directMatch: 'Direct',
    needsTransfer: 'Needs transfer',
    favoritePresetChoice: 'Favorite choice',
    customLookup: 'New departures lookup',
    useFavoritePreset: 'Use favorite preset'
    ,details: 'Platforms'
    ,hideDetails: 'Hide platforms'
    ,useSuggestedStation: 'Use suggested station'
  }
};

function t(key) {
  return translations[state.language]?.[key] || translations.en[key] || key;
}

function setLanguage(language) {
  state.language = language === 'en' ? 'en' : 'sr';
  localStorage.setItem('language', state.language);
  document.documentElement.lang = state.language === 'sr' ? 'sr' : 'en';
}

function resetLocalizedMessages() {
  state.searchMessage = t('searchInitial');
  state.searchResolvedAddress = '';
  state.nearbyMessage = t('nearbyLoading');
  state.nearbyCenterLabel = t('belgradeCenter');
  state.favoritesMessage = t('loadingFavorites');
  state.stopMessage = t('loadingDepartures');
  state.routeMessage = t('routeInitial');
  state.navMessage = t('chooseBothStops');
}

function getToken() {
  const token = sessionStorage.getItem(AUTH_TOKEN_KEY);
  const legacyToken = localStorage.getItem(AUTH_TOKEN_KEY);
  if (legacyToken) {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    if (!token) {
      sessionStorage.setItem(AUTH_TOKEN_KEY, legacyToken);
      return legacyToken;
    }
  }
  return token;
}

function setToken(token) {
  sessionStorage.setItem(AUTH_TOKEN_KEY, token);
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

function clearToken() {
  sessionStorage.removeItem(AUTH_TOKEN_KEY);
  localStorage.removeItem(AUTH_TOKEN_KEY);
}

function decodeTokenPayload() {
  const token = getToken();
  if (!token) {
    return null;
  }

  try {
    const payload = token.split('.')[1];
    return JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
  } catch (error) {
    console.error(error);
    return null;
  }
}

function getCurrentUsername() {
  const payload = decodeTokenPayload();
  return payload && payload.username ? payload.username : null;
}

function isAuthenticated() {
  return Boolean(getToken());
}

function logout() {
  clearToken();
  state.favorites = [];
  state.favoritesMessage = t('favoritesRequireLogin');
  renderView('profile');
}

function formatPublicStopId(stopId) {
  const value = String(stopId || '').trim();
  if (!value) {
    return '';
  }

  if (/^\d+$/.test(value)) {
    const numeric = Number(value);
    if (numeric >= 20000) {
      return String(numeric - 20000);
    }
  }

  return value;
}

function parseCoordinate(value) {
  if (value === null || value === undefined || value === '') {
    return NaN;
  }

  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : NaN;
}

function hasCoordinates(stop) {
  return Number.isFinite(stop.lat) && Number.isFinite(stop.lon);
}

function distanceMeters(left, right) {
  if (!hasCoordinates(left) || !hasCoordinates(right)) {
    return null;
  }

  const earthRadius = 6371000;
  const leftLat = left.lat * Math.PI / 180;
  const rightLat = right.lat * Math.PI / 180;
  const deltaLat = (right.lat - left.lat) * Math.PI / 180;
  const deltaLon = (right.lon - left.lon) * Math.PI / 180;
  const a = Math.sin(deltaLat / 2) ** 2
    + Math.cos(leftLat) * Math.cos(rightLat) * Math.sin(deltaLon / 2) ** 2;
  return Math.round(earthRadius * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
}

function normalizeStop(stop) {
  const rawStopId = String(
    stop.rawStopId ??
      stop.stop_id ??
      stop.station_id ??
      stop.stationId ??
      stop.id ??
      ''
  ).trim();

  const lat = parseCoordinate(stop.lat ?? stop.stop_lat);
  const lon = parseCoordinate(stop.lon ?? stop.stop_lon ?? stop.lng);

  return {
    stationId: String(stop.stationId ?? stop.station_id ?? formatPublicStopId(rawStopId)).trim(),
    rawStopId,
    uid: stop.id ? String(stop.id) : rawStopId,
    name: String(stop.name ?? stop.stop_name ?? `Stop ${formatPublicStopId(rawStopId)}`),
    lat,
    lon,
    distance: stop.distance != null ? Number(stop.distance) : null,
    favoriteName: stop.favoriteName ?? null,
    presetLine: stop.presetLine ?? stop.line ?? null,
    lines: Array.isArray(stop.lines)
      ? stop.lines.map(String)
      : (Array.isArray(stop.shared_lines) ? stop.shared_lines.map(String) : []),
    stationMode: stop.stationMode ?? stop.station_mode ?? null
  };
}

function normalizeTransitLine(line) {
  return String(line || '').trim().toUpperCase();
}

function getTransitLineBase(line) {
  return normalizeTransitLine(line).match(/^\d+/)?.[0] || '';
}

function getStationMode(stop) {
  if (['bus', 'tram', 'mixed'].includes(stop?.stationMode)) {
    return stop.stationMode;
  }

  if (['bus', 'tram', 'mixed'].includes(stop?.station_mode)) {
    return stop.station_mode;
  }

  const lines = Array.isArray(stop?.lines) ? stop.lines.map(normalizeTransitLine).filter(Boolean) : [];

  if (!lines.length) {
    return 'bus';
  }

  const hasTram = lines.some((line) => TRAM_LINES.has(getTransitLineBase(line)));
  const hasBus = lines.some((line) => !TRAM_LINES.has(getTransitLineBase(line)));

  if (hasTram && hasBus) {
    return 'mixed';
  }

  return hasTram ? 'tram' : 'bus';
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function stopDataset(stop) {
  return [
    `data-stop-id="${escapeHtml(stop.stationId || '')}"`,
    `data-stop-raw-id="${escapeHtml(stop.rawStopId || '')}"`,
    `data-stop-name="${escapeHtml(stop.name)}"`,
    `data-stop-lat="${Number.isFinite(stop.lat) ? stop.lat : ''}"`,
    `data-stop-lon="${Number.isFinite(stop.lon) ? stop.lon : ''}"`,
    `data-stop-line="${escapeHtml(stop.presetLine || '')}"`
  ].join(' ');
}

async function getStopDetails(stop) {
  const normalizedStop = normalizeStop(stop);
  if (normalizedStop.lines.length) {
    return normalizedStop;
  }

  let detailedStop = normalizedStop;

  try {
    const data = await apiRequest(`/stops?station_id=${encodeURIComponent(normalizedStop.stationId)}`);
    const match = (data.stops || [])[0];
    if (match) {
      detailedStop = normalizeStop({
        ...normalizedStop,
        ...match,
        station_id: normalizedStop.stationId,
        name: match.stop_name || match.name || normalizedStop.name,
        stop_lat: match.stop_lat ?? normalizedStop.lat,
        stop_lon: match.stop_lon ?? normalizedStop.lon,
        lines: match.lines || []
      });
    }
  } catch (error) {
    detailedStop = normalizedStop;
  }

  if (detailedStop.lines.length) {
    return detailedStop;
  }

  try {
    const data = await apiRequest(`/predict/stop?station_id=${encodeURIComponent(detailedStop.stationId)}`);
    const lines = Array.from(new Set((data.predicted_arrivals || [])
      .filter((arrival) => !arrival.error && !arrival.empty && arrival.line)
      .map((arrival) => String(arrival.line))))
      .sort(compareLineLabels);

    return {
      ...detailedStop,
      lines
    };
  } catch (error) {
    return detailedStop;
  }
}

async function showStopChoice(stop) {
  state.mapPickCandidate = await getStopDetails(stop);
  state.mapPickMode = false;
  state.mapPickOptions = [];
  state.mapPickMessage = '';
  renderView(state.currentView);
}

function getFavoriteForStop(stop) {
  const requestedLine = normalizeLineList(stop?.presetLine || '');
  return state.favorites.find((favorite) => {
    if (favorite.stationId !== stop.stationId) {
      return false;
    }

    const favoriteLine = normalizeLineList(favorite.presetLine || '');
    return requestedLine ? favoriteLine === requestedLine : !favoriteLine;
  }) || null;
}

async function apiRequest(path, options = {}) {
  const { auth = false, body, headers = {}, ...rest } = options;
  const requestHeaders = { ...headers };

  if (body !== undefined) {
    requestHeaders['Content-Type'] = 'application/json';
  }

  if (auth && getToken()) {
    requestHeaders.Authorization = `Bearer ${getToken()}`;
  }

  const response = await fetch(`${API_URL}${path}`, {
    ...rest,
    headers: requestHeaders,
    body: body !== undefined ? JSON.stringify(body) : undefined
  });

  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    payload = {};
  }

  if (response.status === 401) {
    clearToken();
    state.favorites = [];
    throw new Error(payload.error || 'Your session expired. Please log in again.');
  }

  if (!response.ok) {
    throw new Error(payload.error || `Request failed with status ${response.status}`);
  }

  return payload;
}

function createStationIcon(type = 'stop', stop = null) {
  const stationMode = getStationMode(stop);
  const fillHtml = stationMode === 'mixed'
    ? '<span class="stop-marker__fill stop-marker__fill--bus"></span><span class="stop-marker__fill stop-marker__fill--tram"></span>'
    : `<span class="stop-marker__fill stop-marker__fill--${stationMode}"></span>`;

  return L.divIcon({
    className: `stop-marker stop-marker--${type} stop-marker--${stationMode}`,
    html: `<span class="stop-marker__pin">${fillHtml}<span class="stop-marker__dot"></span></span>`,
    iconSize: [30, 38],
    iconAnchor: [15, 34],
    popupAnchor: [0, -32]
  });
}

function createUserLocationIcon() {
  return L.divIcon({
    className: 'user-location-marker',
    html: '<span class="user-location-marker__ring"><span class="user-location-marker__dot"></span></span>',
    iconSize: [34, 34],
    iconAnchor: [17, 17],
    popupAnchor: [0, -18]
  });
}

function createMapPickIcon() {
  return L.divIcon({
    className: 'map-pick-marker',
    html: '<span class="map-pick-marker__pin"><span></span></span>',
    iconSize: [34, 42],
    iconAnchor: [17, 38],
    popupAnchor: [0, -34]
  });
}

function createFavoriteStopIcon(lines) {
  const label = lines.length ? lines.join(', ') : t('favoriteMarker');
  return L.divIcon({
    className: 'favorite-stop-marker',
    html: `
      <span class="favorite-stop-marker__label">${escapeHtml(label)}</span>
      <span class="favorite-stop-marker__pin"><span></span></span>
    `,
    iconSize: [86, 54],
    iconAnchor: [43, 46],
    popupAnchor: [0, -42]
  });
}

function ensureMap() {
  if (!mapContainer || typeof L === 'undefined' || map) {
    return;
  }

  const tileUrl = 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png';

  map = L.map(mapContainer, {
    zoomControl: false,
    maxBounds: STATION_BOUNDS,
    maxBoundsViscosity: 1,
    minZoom: 10
  }).setView([DEFAULT_CENTER.lat, DEFAULT_CENTER.lon], 13);
  state.markerLayer = L.layerGroup().addTo(map);
  state.favoriteMarkerLayer = L.layerGroup().addTo(map);
  state.routeLayer = L.layerGroup().addTo(map);
  state.locationMarker = L.layerGroup().addTo(map);

  L.control.zoom({ position: 'topright' }).addTo(map);
  L.tileLayer(tileUrl, {
    attribution: '&copy; OpenStreetMap &copy; CARTO',
    subdomains: 'abcd',
    maxZoom: 19
  }).addTo(map);

  map.on('click', async (event) => {
    if (!state.mapPickMode) {
      return;
    }

    const coords = { lat: event.latlng.lat, lon: event.latlng.lng };
    state.locationMarker.clearLayers();
    renderDroppedPin(coords);

    try {
      const data = await apiRequest(`/stops/nearby?lat=${coords.lat}&lon=${coords.lon}&radius=450`);
      state.mapPickOptions = (data.stops || []).map(normalizeStop).slice(0, 8);
      state.mapPickMessage = state.mapPickOptions.length ? '' : t('noStopsArea');
      renderStopsOnMap(state.mapPickOptions, false);
      renderDroppedPin(coords);
      state.mapPickMode = false;
    } catch (error) {
      state.mapPickOptions = [];
      state.mapPickMessage = error.message;
      state.mapPickMode = false;
    }

    renderView(state.currentView);
  });
}

function clearMapLayers() {
  if (!map) {
    return;
  }

  state.markerLayer.clearLayers();
  state.favoriteMarkerLayer.clearLayers();
  state.routeLayer.clearLayers();
}

function renderDroppedPin(coords) {
  if (!map || !coords) {
    return;
  }

  state.locationMarker.clearLayers();
  L.marker([coords.lat, coords.lon], { icon: createMapPickIcon() })
    .addTo(state.locationMarker)
    .bindPopup(escapeHtml(t('pickedMapPoint')));
}

function renderUserLocationOnMap(coords) {
  if (!map || !coords || !Number.isFinite(Number(coords.lat)) || !Number.isFinite(Number(coords.lon))) {
    return;
  }

  L.marker([Number(coords.lat), Number(coords.lon)], {
    icon: createUserLocationIcon(),
    zIndexOffset: 1400
  })
    .addTo(state.locationMarker)
    .bindPopup(escapeHtml(t('currentLocationStart')));
}

function renderStopsOnMap(stops, fitBounds = true) {
  if (!map) {
    return;
  }

  clearMapLayers();
  const validStops = stops.filter(hasCoordinates);

  validStops.forEach((stop) => {
    const marker = L.marker([stop.lat, stop.lon], { icon: createStationIcon('stop', stop) }).addTo(state.markerLayer);
    marker.on('click', async () => {
      await showStopChoice(stop);
    });
    marker.bindPopup(
      `<strong>${escapeHtml(stop.name)}</strong><br>${escapeHtml(t('stop'))} ${escapeHtml(stop.stationId)}`
    );
  });

  renderFavoriteStopsOnMap();

  if (fitBounds && validStops.length > 0) {
    const bounds = L.latLngBounds(validStops.map((stop) => [stop.lat, stop.lon]));
    map.fitBounds(bounds.pad(0.2));
  } else if (validStops.length === 1) {
    map.setView([validStops[0].lat, validStops[0].lon], 16);
  }
}

function getFavoriteStopGroups() {
  const groups = new Map();

  state.favorites.filter(hasCoordinates).forEach((favorite) => {
    const key = favorite.stationId;
    const existing = groups.get(key) || {
      ...favorite,
      lines: [],
      favorites: []
    };
    existing.favorites = [...existing.favorites, favorite];
    existing.lines = Array.from(new Set([
      ...existing.lines,
      ...parseLineList(favorite.presetLine || '')
    ])).sort(compareLineLabels);
    groups.set(key, existing);
  });

  return Array.from(groups.values());
}

function renderFavoriteStopsOnMap() {
  if (!map || !state.favoriteMarkerLayer) {
    return;
  }

  state.favoriteMarkerLayer.clearLayers();
  getFavoriteStopGroups().forEach((favorite) => {
    const marker = L.marker([favorite.lat, favorite.lon], {
      icon: createFavoriteStopIcon(favorite.lines),
      zIndexOffset: 1000
    }).addTo(state.favoriteMarkerLayer);

    marker.on('click', () => {
      const stop = {
        ...favorite,
        presetLine: favorite.lines.join(', ')
      };
      if (favorite.lines.length) {
        state.favoriteChoiceStop = stop;
        renderView(state.currentView);
      } else {
        openStop(stop);
      }
    });
    marker.bindPopup(
      `<strong>${escapeHtml(favorite.name)}</strong><br>${escapeHtml(t('favoriteMarker'))}: ${escapeHtml(favorite.lines.join(', ') || t('favoriteMarker'))}`
    );
  });
}

function renderRouteOnMap(directions) {
  if (!map) {
    return;
  }

  clearMapLayers();
  const colors = ['#378ADD'];
  const allPoints = [];

  directions.forEach((direction, index) => {
    const points = direction.stops
      .map((stop) => [Number(stop.stop_lat), Number(stop.stop_lon)])
      .filter((point) => Number.isFinite(point[0]) && Number.isFinite(point[1]));

    if (points.length === 0) {
      return;
    }

    allPoints.push(...points);

    L.polyline(points, {
      color: colors[index % colors.length],
      weight: 4,
      opacity: 0.85
    }).addTo(state.routeLayer);

    const firstStop = direction.stops[0];
    if (firstStop) {
      L.marker([Number(firstStop.stop_lat), Number(firstStop.stop_lon)], {
        icon: createStationIcon('route', { ...firstStop, lines: [state.routeLine] })
      })
        .addTo(state.markerLayer)
        .bindPopup(`<strong>${escapeHtml(direction.headsign)}</strong>`);
    }
  });

  renderFavoriteStopsOnMap();

  if (allPoints.length > 0) {
    map.fitBounds(L.latLngBounds(allPoints).pad(0.12));
  }
}

function departureLookupKey(stationId, line) {
  return `${stationId || ''}|${line || ''}`;
}

function getDeparturesByLine() {
  const departuresByLine = new Map();
  state.navDepartures.forEach((departure) => {
    const key = departureLookupKey(departure.stationId || '', departure.line || '');
    if (!departuresByLine.has(key)) {
      departuresByLine.set(key, []);
    }
    departuresByLine.get(key).push(departure);
  });
  return departuresByLine;
}

function getRouteTransferStationId(route) {
  return route.transfer_to_station_id || route.transfer_station_id || formatPublicStopId(route.transfer_to_stop_id || route.transfer_stop_id || '');
}

function getRouteTransferFromStationId(route) {
  return route.transfer_from_station_id || route.transfer_station_id || formatPublicStopId(route.transfer_from_stop_id || route.transfer_stop_id || '');
}

function getRouteTransferToStationId(route) {
  return getRouteTransferStationId(route);
}

function getDeparturesForRoute(departuresByLine, stationId, line, direction = '') {
  const departures = departuresByLine.get(departureLookupKey(stationId || '', line || '')) || [];
  const routeDirection = String(direction || '').trim();
  if (!routeDirection) {
    return departures;
  }

  const matchingDepartures = departures.filter((departure) =>
    String(departure.direction || '').trim() === routeDirection
  );
  return matchingDepartures.length ? matchingDepartures : departures;
}

function getVisibleNavigationRoutes() {
  return state.navRoutes;
}

function formatNavigationResultsCount(count) {
  if (!count) {
    return t('noRoutesFound');
  }
  return `${count} ${count === 1 ? t('resultFound') : t('resultsFound')}`;
}

function isTransferRoute(route) {
  return route.type === 'transfer' || route.type === 'multi_transfer';
}

function getRouteSecondLines(route) {
  if (!isTransferRoute(route)) {
    return [];
  }

  if (route.type === 'multi_transfer') {
    return [route.line2, route.line3].filter(Boolean).map(String);
  }

  const lines = Array.isArray(route.transferOptions) && route.transferOptions.length
    ? route.transferOptions.map((option) => option.line2)
    : [route.line2];

  return Array.from(new Set(lines.filter(Boolean).map(String))).sort(compareLineLabels);
}

function getRouteFirstLines(route) {
  if (route.type === 'direct') {
    return [route.line].filter(Boolean).map(String);
  }

  const lines = Array.isArray(route.firstLineOptions) && route.firstLineOptions.length
    ? route.firstLineOptions.map((option) => option.line1)
    : [route.line1];

  return Array.from(new Set(lines.filter(Boolean).map(String))).sort(compareLineLabels);
}

function getRouteFavoriteLines(route) {
  const lines = [...getRouteFirstLines(route), ...getRouteSecondLines(route)];
  return normalizeLineList(lines.filter(Boolean).join(', '));
}

function truncateFavoriteName(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (text.length <= 80) {
    return text;
  }
  return text.slice(0, 77).trimEnd() + '...';
}

function getRouteFavoritePayload(route) {
  const stationId = route.from_station_id || route.origin_station_id || state.navFromStop?.stationId || '';
  const fromName = route.from_station_name || state.navFromStop?.name || `${t('stop')} ${stationId}`;
  const toId = route.to_station_id || route.dest_station_id || state.navToStop?.stationId || '';
  const toName = route.to_station_name || state.navToStop?.name || `${t('stop')} ${toId}`;
  const lines = getRouteFavoriteLines(route);
  const label = [fromName, toName].filter(Boolean).join(' -> ');
  const name = truncateFavoriteName(lines ? `${label} (${lines})` : label);

  return {
    name,
    station_id: stationId,
    line: lines || null
  };
}

function renderRouteLineBadge(route) {
  const firstLines = getRouteFirstLines(route);
  const secondLines = getRouteSecondLines(route);
  const label = route.type === 'direct'
    ? route.line
    : [firstLines.join(' / '), ...secondLines].filter(Boolean).join(' -> ');
  return `<div class="route-line-badge">${escapeHtml(label || t('line'))}</div>`;
}

function formatRouteWalk(distanceMetersValue) {
  const distance = Number(distanceMetersValue || 0);
  if (!Number.isFinite(distance) || distance <= 0) {
    return '';
  }

  return `${Math.round(distance)} m`;
}

function stationStopMeta(stationId, extra = '') {
  return [stationId ? `${t('stop')} ${stationId}` : '', extra].filter(Boolean).join(' · ');
}

function renderRouteStationSegment({ name, stationId, icon = 'map-pin', className = '', meta = '' }) {
  const label = name || (stationId ? `${t('stop')} ${stationId}` : t('stop'));
  const details = stationStopMeta(stationId, meta);
  return `
    <span class="${escapeHtml(className)}">
      <i data-lucide="${escapeHtml(icon)}" aria-hidden="true"></i>
      <strong>${escapeHtml(label)}</strong>
      ${details ? `<small>${escapeHtml(details)}</small>` : ''}
    </span>
  `;
}

function renderTransferWalkSegments(fromStop, toStop, walkMeters) {
  const walkLabel = formatRouteWalk(walkMeters);
  const hasWalk = walkLabel && String(fromStop.stationId || '') !== String(toStop.stationId || '');

  if (!hasWalk) {
    return renderRouteStationSegment({
      ...toStop,
      className: 'route-station-pair__transfer'
    });
  }

  return `
    ${renderRouteStationSegment({
      ...fromStop,
      className: 'route-station-pair__transfer',
      meta: `${walkLabel} ${t('walk')}`
    })}
    <i class="route-station-pair__arrow" data-lucide="footprints" aria-hidden="true"></i>
    ${renderRouteStationSegment({
      ...toStop,
      className: 'route-station-pair__transfer',
      meta: `${walkLabel} ${t('walk')}`
    })}
  `;
}

function renderRouteStopsRow(route) {
  const fromName = route.from_station_name || state.navFromStop?.name || '';
  const fromId = route.from_station_id || route.origin_station_id || state.navFromStop?.stationId || '';
  const transferName = route.transfer_at || '';
  const transferFromId = getRouteTransferFromStationId(route);
  const transferToId = getRouteTransferToStationId(route);
  const toName = route.to_station_name || state.navToStop?.name || '';
  const toId = route.to_station_id || route.dest_station_id || state.navToStop?.stationId || '';
  const firstTransfer = {
    name: route.transfer1_from_stop_name || route.transfer1_at || '',
    stationId: route.transfer1_from_station_id || formatPublicStopId(route.transfer1_from_stop_id || '')
  };
  const firstTransferBoarding = {
    name: route.transfer1_to_stop_name || route.transfer1_at || '',
    stationId: route.transfer1_to_station_id || formatPublicStopId(route.transfer1_to_stop_id || '')
  };
  const secondTransfer = {
    name: route.transfer2_from_stop_name || route.transfer2_at || '',
    stationId: route.transfer2_from_station_id || formatPublicStopId(route.transfer2_from_stop_id || '')
  };
  const secondTransferBoarding = {
    name: route.transfer2_to_stop_name || route.transfer2_at || '',
    stationId: route.transfer2_to_station_id || formatPublicStopId(route.transfer2_to_stop_id || '')
  };
  return `
    <div class="route-station-pair ${isTransferRoute(route) ? 'route-station-pair--transfer' : ''}">
      ${renderRouteStationSegment({ name: fromName, stationId: fromId })}
      <i class="route-station-pair__arrow" data-lucide="arrow-right" aria-hidden="true"></i>
      ${route.type === 'multi_transfer' ? `
        ${renderTransferWalkSegments(firstTransfer, firstTransferBoarding, route.transfer1_walk_m)}
        <i class="route-station-pair__arrow" data-lucide="arrow-right" aria-hidden="true"></i>
        ${renderTransferWalkSegments(secondTransfer, secondTransferBoarding, route.transfer2_walk_m)}
        <i class="route-station-pair__arrow" data-lucide="arrow-right" aria-hidden="true"></i>
      ` : route.type === 'transfer' ? `
        ${renderTransferWalkSegments(
          {
            name: route.transfer_from_stop_name || transferName,
            stationId: transferFromId
          },
          {
            name: route.transfer_to_stop_name || transferName,
            stationId: transferToId
          },
          route.transfer_walk_m
        )}
        <i class="route-station-pair__arrow" data-lucide="arrow-right" aria-hidden="true"></i>
      ` : ''}
      ${renderRouteStationSegment({ name: toName, stationId: toId, icon: 'flag' })}
    </div>
  `;
}

function renderDepartureRows(departures, transfer = false, showLine = false) {
  return departures.length
    ? departures.slice(0, 3).map((departure) => `
      <div class="arrival-row">
        <span class="arrival-chip ${transfer ? 'arrival-chip--transfer' : ''}">${escapeHtml(departure.mins_remaining)} min</span>
        <span>${showLine && departure.line ? `${escapeHtml(departure.line)} · ` : ''}${escapeHtml(departure.arrival_time)} · ${escapeHtml(departure.direction || '')}</span>
      </div>
    `).join('')
    : `<div class="settings-hint">${escapeHtml(t('noDepartures60'))}</div>`;
}

function renderDepartureGroup(title, departures, transfer = false) {
  const lines = Array.from(new Set(departures.map((departure) => departure.line).filter(Boolean).map(String))).sort(compareLineLabels);
  const line = lines.join(' / ');
  return `
    <div class="arrival-list ${transfer ? 'arrival-list--transfer' : ''}">
      <div class="departure-group-header">
        ${line ? `<span class="departure-line-badge ${transfer ? 'departure-line-badge--transfer' : ''}">${escapeHtml(line)}</span>` : ''}
        <strong class="route-card__count">${escapeHtml(title)}</strong>
      </div>
      ${renderDepartureRows(departures, transfer, lines.length > 1)}
    </div>
  `;
}

function renderTransferDepartureGroups(route, departuresByLine, transferStationId) {
  const transferLegs = route.type === 'multi_transfer'
    ? [
        {
          line: route.line2,
          stationId: route.transfer1_to_station_id || formatPublicStopId(route.transfer1_to_stop_id || ''),
          name: route.transfer1_to_stop_name || ''
        },
        {
          line: route.line3,
          stationId: route.transfer2_to_station_id || formatPublicStopId(route.transfer2_to_stop_id || ''),
          name: route.transfer2_to_stop_name || ''
        }
      ]
    : getRouteSecondLines(route).map((line) => ({
        line,
        stationId: transferStationId,
        name: route.transfer_to_stop_name || route.transfer_at || ''
      }));

  if (!transferLegs.length) {
    return '';
  }

  return `
    <div class="arrival-list arrival-list--transfer">
      ${transferLegs.map((leg) => {
        const departures = getDeparturesForRoute(departuresByLine, leg.stationId, leg.line, '');
        const title = [leg.name || t('transferAt'), leg.stationId ? `${t('stop')} ${leg.stationId}` : '']
          .filter(Boolean)
          .join(' · ');
        return `
          <div class="transfer-departure-option">
            <div class="departure-group-header">
              <span class="departure-line-badge departure-line-badge--transfer">${escapeHtml(leg.line)}</span>
              <strong class="route-card__count">${escapeHtml(t('nextFromTransfer'))}: ${escapeHtml(title)}</strong>
            </div>
            ${renderDepartureRows(departures, true)}
          </div>
        `;
      }).join('')}
    </div>
  `;
}

function getRouteCardKey(route) {
  return navigationRouteGroupKey(route, false);
}

function formatNextDepartureSummary(departures) {
  const next = departures[0];
  if (!next) {
    return t('noDepartures60');
  }

  return `${next.mins_remaining} min · ${next.arrival_time}`;
}

function renderRouteCollapsedMeta(route, departures) {
  const parts = [formatNextDepartureSummary(departures)];
  if (isTransferRoute(route) && route.transfer_at) {
    parts.push(`${t('transferAt')}: ${route.transfer_at}`);
  }
  return parts.filter(Boolean).join(' · ');
}

function getOriginDeparturesForRoute(route, departuresByLine, stationId) {
  return getRouteFirstLines(route)
    .flatMap((line) => getDeparturesForRoute(departuresByLine, stationId, line, route.direction))
    .sort((left, right) => Number(left.mins_remaining || 9999) - Number(right.mins_remaining || 9999));
}

async function fetchLineDirectionsForMap(line, stationId) {
  if (!line) {
    return [];
  }

  const routeParams = new URLSearchParams({ line });
  if (stationId) {
    routeParams.set('station_id', stationId);
  }
  const cacheKey = routeParams.toString();
  if (routeGeometryCache.has(cacheKey)) {
    return routeGeometryCache.get(cacheKey);
  }
  const data = await apiRequest(`/route?${routeParams.toString()}`);
  const directions = data.directions || [];
  routeGeometryCache.set(cacheKey, directions);
  return directions;
}

function getTransferStopForMap(route) {
  const lat = parseCoordinate(route.transfer_to_stop_lat ?? route.transfer_stop_lat);
  const lon = parseCoordinate(route.transfer_to_stop_lon ?? route.transfer_stop_lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return null;
  }

  return {
    stationId: getRouteTransferStationId(route),
    name: route.transfer_at || t('transferAt'),
    lat,
    lon
  };
}

function getTransferStopsForMap(route) {
  if (route.type === 'multi_transfer') {
    return [
      {
        stationId: route.transfer1_to_station_id || formatPublicStopId(route.transfer1_to_stop_id || ''),
        name: route.transfer1_at || route.transfer1_to_stop_name || t('transferAt'),
        lat: parseCoordinate(route.transfer1_to_stop_lat),
        lon: parseCoordinate(route.transfer1_to_stop_lon),
        lines: [route.line1, route.line2]
      },
      {
        stationId: route.transfer2_to_station_id || formatPublicStopId(route.transfer2_to_stop_id || ''),
        name: route.transfer2_at || route.transfer2_to_stop_name || t('transferAt'),
        lat: parseCoordinate(route.transfer2_to_stop_lat),
        lon: parseCoordinate(route.transfer2_to_stop_lon),
        lines: [route.line2, route.line3]
      }
    ].filter((stop) => Number.isFinite(stop.lat) && Number.isFinite(stop.lon));
  }

  const transferStop = route.type === 'transfer' ? getTransferStopForMap(route) : null;
  return transferStop ? [{ ...transferStop, lines: [route.line1, route.line2] }] : [];
}

function stopMatchesStation(stop, stationId) {
  const requestedId = String(stationId || '').trim();
  const rawStopId = String(stop?.stop_id || '').trim();
  return Boolean(requestedId) && (
    rawStopId === requestedId
    || formatPublicStopId(rawStopId) === requestedId
  );
}

function getRouteSegmentPoints(directions, fromStationId, toStationId) {
  const candidates = [];

  directions.forEach((direction) => {
    const stops = direction.stops || [];
    const fromIndex = stops.findIndex((stop) => stopMatchesStation(stop, fromStationId));
    const toIndex = stops.findIndex((stop) => stopMatchesStation(stop, toStationId));

    if (fromIndex < 0 || toIndex < 0 || fromIndex >= toIndex) {
      return;
    }

    const points = stops.slice(fromIndex, toIndex + 1)
      .map((stop) => [Number(stop.stop_lat), Number(stop.stop_lon)])
      .filter((point) => Number.isFinite(point[0]) && Number.isFinite(point[1]));

    if (points.length > 1) {
      candidates.push(points);
    }
  });

  return candidates.sort((left, right) => left.length - right.length)[0] || [];
}

async function renderNavigationRouteOnMap(route) {
  if (!map || !route) {
    return;
  }

  const routeStartStop = normalizeStop({
    station_id: route.from_station_id || route.origin_station_id || state.navFromStop?.stationId,
    stop_id: route.origin_stop_id || route.from_station_id || state.navFromStop?.rawStopId,
    name: route.from_station_name || state.navFromStop?.name,
    stop_lat: route.from_stop_lat ?? state.navFromStop?.lat,
    stop_lon: route.from_stop_lon ?? state.navFromStop?.lon
  });
  const routeEndStop = normalizeStop({
    station_id: route.to_station_id || route.dest_station_id || state.navToStop?.stationId,
    stop_id: route.dest_stop_id || route.to_station_id || state.navToStop?.rawStopId,
    name: route.to_station_name || state.navToStop?.name,
    stop_lat: route.to_stop_lat ?? state.navToStop?.lat,
    stop_lon: route.to_stop_lon ?? state.navToStop?.lon
  });
  const startLines = isTransferRoute(route) ? [route.line1] : [route.line];
  const endLines = route.type === 'multi_transfer' ? [route.line3] : route.type === 'transfer' ? [route.line2] : [route.line];
  const routeStops = [
    { ...routeStartStop, lines: startLines },
    { ...routeEndStop, lines: endLines }
  ].filter((stop) => stop && hasCoordinates(stop));
  const transferStops = getTransferStopsForMap(route);
  const allPoints = [];
  const colors = ['#185FA5', '#D94A38'];

  clearMapLayers();
  state.locationMarker.clearLayers();

  const legs = route.type === 'direct'
    ? [{
      line: route.line,
      fromStationId: route.origin_station_id || route.from_station_id || state.navFromStop?.stationId,
      toStationId: route.dest_station_id || route.to_station_id || state.navToStop?.stationId
    }]
    : route.type === 'multi_transfer'
      ? [
        {
          line: route.line1,
          fromStationId: route.origin_station_id || route.from_station_id || state.navFromStop?.stationId,
          toStationId: route.transfer1_from_station_id || formatPublicStopId(route.transfer1_from_stop_id || '')
        },
        {
          line: route.line2,
          fromStationId: route.transfer1_to_station_id || formatPublicStopId(route.transfer1_to_stop_id || ''),
          toStationId: route.transfer2_from_station_id || formatPublicStopId(route.transfer2_from_stop_id || '')
        },
        {
          line: route.line3,
          fromStationId: route.transfer2_to_station_id || formatPublicStopId(route.transfer2_to_stop_id || ''),
          toStationId: route.dest_station_id || route.to_station_id || state.navToStop?.stationId
        }
      ]
    : [
      {
        line: route.line1,
        fromStationId: route.origin_station_id || route.from_station_id || state.navFromStop?.stationId,
        toStationId: getRouteTransferFromStationId(route)
      },
      {
        line: route.line2,
        fromStationId: getRouteTransferToStationId(route),
        toStationId: route.dest_station_id || route.to_station_id || state.navToStop?.stationId
      }
    ];

  const legDirections = await Promise.all(legs.map((leg) => fetchLineDirectionsForMap(leg.line, leg.fromStationId)));
  legDirections.forEach((directions, legIndex) => {
    const leg = legs[legIndex];
    const points = getRouteSegmentPoints(directions, leg.fromStationId, leg.toStationId);
    if (!points.length) {
      return;
    }

    allPoints.push(...points);
    L.polyline(points, {
      color: colors[legIndex % colors.length],
      weight: 5,
      opacity: 0.86
    }).addTo(state.routeLayer);
  });

  routeStops.forEach((stop) => {
    L.marker([stop.lat, stop.lon], { icon: createStationIcon('route', stop) })
      .addTo(state.markerLayer)
      .bindPopup(`<strong>${escapeHtml(stop.name)}</strong><br>${escapeHtml(t('stop'))} ${escapeHtml(stop.stationId)}`);
    allPoints.push([stop.lat, stop.lon]);
  });

  if (state.navFromLocation) {
    renderUserLocationOnMap(state.navFromLocation);
    allPoints.push([Number(state.navFromLocation.lat), Number(state.navFromLocation.lon)]);
  }

  transferStops.forEach((transferStop) => {
    L.marker([transferStop.lat, transferStop.lon], {
      icon: createStationIcon('transfer', transferStop),
      zIndexOffset: 1200
    })
      .addTo(state.markerLayer)
      .bindPopup(`<strong>${escapeHtml(t('transferAt'))}: ${escapeHtml(transferStop.name)}</strong><br>${escapeHtml(t('stop'))} ${escapeHtml(transferStop.stationId)}`);
    allPoints.push([transferStop.lat, transferStop.lon]);
  });

  renderFavoriteStopsOnMap();

  if (allPoints.length) {
    map.fitBounds(L.latLngBounds(allPoints).pad(0.12));
  }
}

function updateMapForCurrentView() {
  if (!map) {
    return;
  }

  if (state.mapPickMode || state.mapPickCandidate || state.mapPickOptions.length || state.mapPickMessage) {
    return;
  }

  if (state.currentView === 'home') {
    renderStopsOnMap(state.nearbyStops.slice(0, 12), true);
    return;
  }

  if (state.currentView === 'search') {
    if (state.searchResults.length) {
      renderStopsOnMap(state.searchResults, true);
      const focusedStop = state.searchResults.find(hasCoordinates);
      if (focusedStop) {
        setTimeout(() => {
          map.invalidateSize();
          map.setView([focusedStop.lat, focusedStop.lon], 16);
        }, 0);
      }
    } else {
      clearMapLayers();
      map.setView([DEFAULT_CENTER.lat, DEFAULT_CENTER.lon], 13);
    }
    return;
  }

  if (state.currentView === 'navigate') {
    const navStops = [state.navFromStop, state.navToStop].filter((stop) => stop && hasCoordinates(stop));
    if (navStops.length) {
      renderStopsOnMap(navStops, true);
      if (state.navFromLocation) {
        renderUserLocationOnMap(state.navFromLocation);
      }
    } else {
      clearMapLayers();
      if (state.navFromLocation) {
        renderUserLocationOnMap(state.navFromLocation);
        map.setView([Number(state.navFromLocation.lat), Number(state.navFromLocation.lon)], 15);
        return;
      }
      map.setView([DEFAULT_CENTER.lat, DEFAULT_CENTER.lon], 13);
    }
    return;
  }

  if (state.currentView === 'stop') {
    if (state.routeDirections.length) {
      renderRouteOnMap(state.routeDirections);
      return;
    }

    if (state.currentStop && hasCoordinates(state.currentStop)) {
      renderStopsOnMap([state.currentStop], true);
      map.setView([state.currentStop.lat, state.currentStop.lon], 16);
      return;
    }
  }

  clearMapLayers();
  map.setView([DEFAULT_CENTER.lat, DEFAULT_CENTER.lon], 13);
}

function syncShell() {
  const standaloneAuth = state.currentView === 'auth';
  const showMap = state.currentView === 'home' || state.currentView === 'search' || state.currentView === 'navigate' || state.currentView === 'stop';
  const mapAnchor = document.getElementById('map-anchor');
  if (!standaloneAuth && showMap && mapAnchor) {
    mapAnchor.appendChild(mapContainer);
  } else if (appShell && mapContainer.parentElement !== appShell) {
    appShell.insertBefore(mapContainer, bottomNav);
  }

  mapContainer.style.display = !standaloneAuth && showMap ? '' : 'none';
  bottomNav.style.display = standaloneAuth ? 'none' : '';
  document.body.classList.toggle('auth-page', standaloneAuth);
  document.body.classList.toggle('map-page', !standaloneAuth && showMap);

  if (map && !standaloneAuth && showMap) {
    setTimeout(() => map.invalidateSize(), 0);
  }
}

function setActiveNav() {
  const navView = NAV_VIEWS.has(state.currentView) ? state.currentView : state.previousView;
  document.querySelectorAll('.nav-btn').forEach((button) => {
    const isActive = button.dataset.target === navView;
    button.classList.toggle('active', isActive);
  });
}

function updateNavLabels() {
  const labels = {
    home: t('navHome'),
    search: t('navSearch'),
    navigate: t('navNavigate'),
    favorites: t('navFavorites'),
    profile: t('navProfile')
  };

  document.querySelectorAll('.nav-btn').forEach((button) => {
    const label = button.querySelector('span');
    if (label && labels[button.dataset.target]) {
      label.textContent = labels[button.dataset.target];
      button.setAttribute('aria-label', labels[button.dataset.target]);
    }
  });
}

function isLoadingMessage(message) {
  return /loading|ucitavanje|trazenje|finding|searching|resolving/i.test(String(message || ''));
}

function renderListMessage(message, options = {}) {
  const icon = options.icon || (isLoadingMessage(message) ? 'search' : 'bus-front');
  const action = options.action || '';
  const skeleton = isLoadingMessage(message)
    ? '<div class="empty-state__skeleton"><span></span><span></span><span></span></div>'
    : '';
  return `
    <div class="empty-state">
      <span class="empty-state__icon" aria-hidden="true"><i data-lucide="${escapeHtml(icon)}"></i></span>
      <p>${escapeHtml(message)}</p>
      ${skeleton}
      ${action}
    </div>
  `;
}

function renderField({ id, label, icon, type = 'text', value = '', autocomplete = '', inputmode = '' }) {
  return `
    <label class="field-control" for="${escapeHtml(id)}">
      <span class="field-control__label">${escapeHtml(label)}</span>
      <span class="search-box">
        <i data-lucide="${escapeHtml(icon)}" aria-hidden="true"></i>
        <input
          type="${escapeHtml(type)}"
          id="${escapeHtml(id)}"
          placeholder="${escapeHtml(label)}"
          value="${escapeHtml(value)}"
          ${autocomplete ? `autocomplete="${escapeHtml(autocomplete)}"` : ''}
          ${inputmode ? `inputmode="${escapeHtml(inputmode)}"` : ''}
        >
      </span>
    </label>
  `;
}

function renderStopList(stops, emptyMessage) {
  if (!stops.length) {
    return renderListMessage(emptyMessage, { icon: 'map-pin' });
  }

  return stops
    .map((stop) => {
      const distance = stop.distance != null ? `<span>${escapeHtml(stop.distance)} ${escapeHtml(t('away'))}</span>` : '';
      const lines = (stop.lines && stop.lines.length ? stop.lines : parseLineList(stop.presetLine || '')).slice(0, 5);
      return `
        <button class="stop-card" data-action="open-stop" ${stopDataset(stop)}>
          <span class="stop-card__badge">${escapeHtml(stop.stationId)}</span>
          <span class="stop-card__body">
            <span class="stop-card__title">${escapeHtml(stop.name)}</span>
            <span class="stop-card__meta">${escapeHtml(t('stop'))} ${escapeHtml(stop.stationId)} ${distance}</span>
            ${lines.length ? `<span class="stop-card__lines">${lines.map((line) => `<strong>${escapeHtml(line)}</strong>`).join('')}</span>` : ''}
          </span>
        </button>
      `;
    })
    .join('');
}

function renderNavSuggestions(type, items, emptyMessage) {
  if (!items.length) {
    if (type === 'to') {
      return '';
    }
    return `<div class="nav-suggestions nav-suggestions--empty">${escapeHtml(emptyMessage)}</div>`;
  }

  return `
    <div class="nav-suggestions">
      ${items.map((stop) => {
        if (stop.isGroup) {
          const expandedKey = `${type}:${stop.groupKey}`;
          const isExpanded = state.navExpandedSuggestionKeys.has(expandedKey);
          const platformIds = (stop.stationIds || []).join(', ');
          return `
            <div class="nav-suggestion-group">
              <button
                class="nav-suggestion"
                type="button"
                data-action="${type === 'from' ? 'select-nav-from-group' : 'select-nav-to-group'}"
                data-group-key="${escapeHtml(stop.groupKey)}"
              >
                <span class="nav-suggestion__topline">
                  <span class="nav-suggestion__title">${escapeHtml(stop.name)}</span>
                </span>
                <span class="nav-suggestion__meta">${escapeHtml(t('stop'))} ${escapeHtml(platformIds)}</span>
              </button>
              <button class="ghost-action" type="button" data-action="toggle-nav-suggestion-group" data-suggestion-type="${escapeHtml(type)}" data-group-key="${escapeHtml(stop.groupKey)}">
                ${escapeHtml(isExpanded ? t('hideDetails') : t('details'))}
              </button>
              ${isExpanded ? `
                <div class="nav-suggestion-platforms">
                  ${stop.platforms.map((platform) => `
                    <button class="ghost-action" type="button" data-action="${type === 'from' ? 'select-nav-from' : 'select-nav-to'}" ${stopDataset(platform)}>
                      ${escapeHtml(`${platform.name} (${platform.stationId})`)}
                    </button>
                  `).join('')}
                </div>
              ` : ''}
            </div>
          `;
        }

        const fromLines = (state.navFromStop?.lines || []).map(String);
        const sharedLines = Array.isArray(stop.sharedLines) && stop.sharedLines.length
          ? stop.sharedLines.map(String)
          : (stop.lines || []).filter((line) => fromLines.includes(String(line))).map(String);
        const isDirectDestination = type === 'to' && sharedLines.length;
        return `
          <button
            class="nav-suggestion"
            type="button"
            data-action="${type === 'from' ? 'select-nav-from' : 'select-nav-to'}"
            ${stopDataset(stop)}
          >
            <span class="nav-suggestion__topline">
              <span class="nav-suggestion__title">${escapeHtml(`${stop.name} (${stop.stationId})`)}</span>
              ${type === 'to' ? `<span class="match-pill ${isDirectDestination ? 'match-pill--direct' : 'match-pill--transfer'}">${escapeHtml(isDirectDestination ? t('directMatch') : t('needsTransfer'))}</span>` : ''}
            </span>
            <span class="nav-suggestion__meta">${escapeHtml(t('stop'))} ${escapeHtml(stop.stationId)}</span>
            ${
              type === 'to' && sharedLines.length
                ? `
                  <span class="nav-suggestion__lines">
                    <span>${escapeHtml(t('sharedLines'))}</span>
                    ${sharedLines.slice(0, 6).map((line) => `<strong>${escapeHtml(line)}</strong>`).join('')}
                  </span>
                `
                : ''
            }
          </button>
        `;
      }).join('')}
    </div>
  `;
}

function formatStationLabel(stop) {
  if (!stop) {
    return '';
  }
  return `${stop.name} (${stop.stationId})`;
}

function extractStationIdFromLabel(query) {
  const match = String(query || '').match(/\((\d+)\)\s*$/);
  return match ? match[1] : '';
}

function looksLikeStreetAddress(value) {
  return /\d+/.test(String(value || ''));
}

function getAvailableArrivalLines() {
  const upcomingArrivals = state.arrivals.filter((arrival) => Number(arrival.mins_remaining) <= 60);
  const registeredLines = Array.isArray(state.currentStop?.lines)
    ? state.currentStop.lines.map((line) => String(line))
    : [];

  return Array.from(
    new Set([
      ...registeredLines,
      ...upcomingArrivals.map((arrival) => String(arrival.line || t('unknown')))
    ])
  ).sort(compareLineLabels);
}

function renderDepartureLineButton() {
  const label = state.selectedDepartureLines.length
    ? state.selectedDepartureLines.join(', ')
    : t('chooseLines');

  return `
    <button class="nearby-toggle" data-action="open-sheet" data-sheet="departure-lines">
      <span class="nearby-toggle__label">${escapeHtml(label)}</span>
      <i class="nearby-toggle__icon" data-lucide="chevron-up"></i>
    </button>
  `;
}

function renderFavoritesList() {
  if (!isAuthenticated()) {
    return `
      <div class="empty-state">
        <p>${escapeHtml(t('noFavoritesPublic'))}</p>
        <div class="stack-actions">
          <button class="brutalist-btn" data-action="open-auth" data-mode="login" data-return="favorites">${escapeHtml(t('logIn'))}</button>
          <button class="brutalist-btn outline" data-action="open-auth" data-mode="register" data-return="favorites">${escapeHtml(t('createAccount'))}</button>
        </div>
      </div>
    `;
  }

  if (!state.favorites.length) {
    return renderListMessage(state.favoritesMessage);
  }

  return state.favorites
    .map((favorite) => {
      const favoriteName = favorite.favoriteName || favorite.name;
      if (state.editingFavoriteName === favoriteName) {
        return `
          <div class="bus-item bus-item--edit">
            <form class="favorite-edit-form" data-favorite-name="${escapeHtml(favoriteName)}">
              <label class="inline-field">
                <span>${escapeHtml(t('favoriteName'))}</span>
                <input class="inline-input" name="name" type="text" placeholder="${escapeHtml(t('favoriteName'))}" value="${escapeHtml(favoriteName)}">
              </label>
              <label class="inline-field">
                <span>${escapeHtml(t('stationNumber'))}</span>
                <input class="inline-input" name="station_id" type="text" inputmode="numeric" placeholder="${escapeHtml(t('stationNumber'))}" value="${escapeHtml(favorite.stationId)}">
              </label>
              <label class="inline-field">
                <span>${escapeHtml(t('linePlaceholder'))}</span>
                <input class="inline-input" name="line" type="text" placeholder="${escapeHtml(t('linePlaceholder'))}" value="${escapeHtml(favorite.presetLine || '')}">
              </label>
              ${state.favoriteEditMessage ? `<div class="error-banner">${escapeHtml(state.favoriteEditMessage)}</div>` : ''}
              <div class="favorite-actions favorite-actions--row">
                <button class="favorite-save-btn" type="submit"><i data-lucide="save"></i><span>${escapeHtml(t('save'))}</span></button>
                <button class="ghost-action" type="button" data-action="cancel-edit-favorite">${escapeHtml(t('cancel'))}</button>
              </div>
            </form>
          </div>
        `;
      }

      return `
        <div class="bus-item">
          <button class="bus-item-main" data-action="open-stop" ${stopDataset(favorite)}>
            <span class="favorite-icon" aria-hidden="true"><i data-lucide="map-pin"></i></span>
            <div class="bus-info">
              <strong>${escapeHtml(favoriteName)}</strong>
              <span>${escapeHtml(t('stop'))} ${escapeHtml(favorite.stationId)}</span>
              ${favorite.presetLine ? `<span class="preset-line">${escapeHtml(t('presetLine'))} ${escapeHtml(favorite.presetLine)}</span>` : ''}
            </div>
          </button>
          <div class="favorite-actions">
            ${
              favorite.presetLine
                ? `<button class="ghost-action" data-action="open-favorite-preset" ${stopDataset(favorite)}>${escapeHtml(t('openPreset'))}</button>`
                : ''
            }
            <button class="ghost-action" data-action="edit-favorite" data-favorite-name="${escapeHtml(favoriteName)}">${escapeHtml(t('edit'))}</button>
            <button class="ghost-action" data-action="delete-favorite" data-favorite-name="${escapeHtml(favoriteName)}">${escapeHtml(t('remove'))}</button>
          </div>
        </div>
      `;
    })
    .join('');
}

function renderArrivals() {
  if (!state.arrivals.length) {
    return renderListMessage(state.stopMessage);
  }

  if (state.arrivals[0].error) {
    return renderListMessage(state.arrivals[0].error);
  }

  if (state.arrivals[0].empty) {
    return renderListMessage(`${t('noPlannedPrefix')} ${state.arrivals[0].stop_name}.`);
  }

  const upcomingArrivals = state.arrivals.filter((arrival) => Number(arrival.mins_remaining) <= 60);
  if (!upcomingArrivals.length) {
    return renderListMessage(t('noDepartures60'));
  }

  if (!state.selectedDepartureLines.length) {
    return `
      <div class="stack-gap">
        ${renderDepartureLineButton()}
        ${renderListMessage(t('selectLines'))}
      </div>
    `;
  }

  const filteredArrivals = upcomingArrivals.filter((arrival) =>
    state.selectedDepartureLines.includes(String(arrival.line || t('unknown')))
  );
  const groups = new Map();
  filteredArrivals.forEach((arrival) => {
    const line = arrival.line || t('unknown');
    if (!groups.has(line)) {
      groups.set(line, []);
    }
    groups.get(line).push(arrival);
  });

  return `
    <div class="stack-gap">
      ${renderDepartureLineButton()}
      ${
        filteredArrivals.length
          ? Array.from(groups.entries())
              .sort(([leftLine], [rightLine]) => compareLineLabels(leftLine, rightLine))
              .map(([line, arrivals]) => `
      <div class="route-card">
        <div class="route-card__header">
          <div>
            <div class="route-card__line">${escapeHtml(t('line'))} ${escapeHtml(line)}</div>
            <div class="route-card__direction">${escapeHtml(arrivals[0].direction || t('directionUnavailable'))}</div>
          </div>
          <button class="ghost-action" data-action="show-route" data-line="${escapeHtml(line)}">${escapeHtml(t('showRoute'))}</button>
        </div>
        <div class="arrival-list">
          ${arrivals
            .map((arrival) => `
              <div class="arrival-row">
                <span class="arrival-chip">${escapeHtml(arrival.mins_remaining)} min</span>
                <span>${escapeHtml(arrival.arrival_time)}</span>
              </div>
            `)
            .join('')}
        </div>
      </div>
    `)
              .join('')
          : renderListMessage(t('noSelectedDepartures'))
      }
    </div>
  `;
}

function renderRouteDirections() {
  if (!state.routeLine) {
    return renderListMessage(state.routeMessage);
  }

  if (!state.routeDirections.length) {
    return renderListMessage(state.routeMessage);
  }

  return `
    <div class="route-summary">
      ${state.routeDirections
        .map((direction) => `
          <div class="route-card">
            <div class="route-card__header">
              <div>
                <div class="route-card__line">${escapeHtml(state.routeLine)}</div>
                <div class="route-card__direction">${escapeHtml(direction.headsign || t('direction'))}</div>
              </div>
              <span class="route-card__count">${direction.stops.length} ${escapeHtml(t('stops'))}</span>
            </div>
            <div class="route-stops">
              ${direction.stops
                .slice(0, 10)
                .map((stop) => `<span>${escapeHtml(stop.stop_name)}</span>`)
                .join('')}
              ${direction.stops.length > 10 ? '<span>...</span>' : ''}
            </div>
          </div>
        `)
        .join('')}
    </div>
  `;
}

function renderNavigationResults() {
  if (!state.navRoutes.length) {
    return renderListMessage(state.navMessage);
  }

  const departuresByLine = getDeparturesByLine();
  const visibleRoutes = getVisibleNavigationRoutes();

  if (!visibleRoutes.length) {
    return renderListMessage(t('noDepartures60'));
  }

  return `
    <div class="stack-gap">
      <div class="section-label">${escapeHtml(t('routeOptions'))}</div>
      ${visibleRoutes
        .map((route) => {
          const stationId = route.origin_station_id || route.from_station_id || state.navFromStop?.stationId || '';
          const departures = getOriginDeparturesForRoute(route, departuresByLine, stationId);
          const transferStationId = getRouteTransferStationId(route);
          const routeIndex = state.navRoutes.indexOf(route);
          const routeKey = getRouteCardKey(route);
          const isExpanded = state.navExpandedRouteKeys.has(routeKey);
          return `
            <div class="route-card route-card--${route.type === 'direct' ? 'direct' : 'transfer'} ${isExpanded ? 'route-card--expanded' : ''}">
              <button
                class="route-card__summary"
                type="button"
                data-action="toggle-nav-route-details"
                data-route-key="${escapeHtml(routeKey)}"
                aria-expanded="${isExpanded ? 'true' : 'false'}"
              >
                <span class="route-card__summary-main">
                  ${renderRouteLineBadge(route)}
                  <span class="route-type-pill route-type-pill--${route.type === 'direct' ? 'direct' : 'transfer'}">${escapeHtml(route.type === 'direct' ? t('directRoute') : t('transferRoute'))}</span>
                </span>
                <span class="route-card__summary-meta">${escapeHtml(renderRouteCollapsedMeta(route, departures))}</span>
                <i data-lucide="chevron-${isExpanded ? 'up' : 'down'}" aria-hidden="true"></i>
              </button>
              ${isExpanded ? `
                <div class="route-card__details">
                  <div class="route-card__direction">
                    ${route.type === 'direct'
                      ? `${escapeHtml(route.direction || t('directionUnavailable'))} · ${escapeHtml(route.stops_count)} ${escapeHtml(t('stops'))}`
                      : `${escapeHtml(t('transferAt'))}: ${escapeHtml(route.transfer_at || '')}`}
                  </div>
                  ${renderRouteStopsRow(route)}
                </div>
                <div class="route-card__actions">
                  <button class="ghost-action" type="button" data-action="show-nav-route" data-route-index="${routeIndex}">
                    ${escapeHtml(t('showOnMap'))}
                  </button>
                  <button class="ghost-action" type="button" data-action="save-nav-route" data-route-index="${routeIndex}">
                    <i data-lucide="save"></i>${escapeHtml(t('saveRoute'))}
                  </button>
                </div>
                <div class="route-departure-groups">
                  ${renderDepartureGroup(t('nextFromOrigin'), departures)}
                  ${isTransferRoute(route) ? renderTransferDepartureGroups(route, departuresByLine, transferStationId) : ''}
                </div>
              ` : ''}
            </div>
          `;
        })
        .join('')}
    </div>
  `;
}

function renderRecentLines() {
  if (!state.recentLines.length) {
    return renderListMessage(t('noRecentLines'));
  }

  return `
    <div class="line-filter-bar recent-lines-panel">
      ${state.recentLines
        .map((item, index) => `
          <button class="line-filter-chip" data-action="open-recent-item" data-recent-index="${index}">
            ${escapeHtml(item.type === 'stop' ? `${item.stationId} / ${item.presetLine}` : item.line)}
          </button>
        `)
        .join('')}
    </div>
  `;
}

function renderRecentLinesCarousel() {
  if (!state.recentLines.length) {
    return `
      <section class="recent-carousel-section">
        <div class="section-heading">
          <div class="section-label">${escapeHtml(t('recentLines'))}</div>
        </div>
        ${renderListMessage(t('noRecentLines'))}
      </section>
    `;
  }

  return `
    <section class="recent-carousel-section">
      <div class="section-heading">
        <div class="section-label">${escapeHtml(t('recentLines'))}</div>
        <button class="ghost-action compact-action" data-action="clear-recent-lines">${escapeHtml(t('clearRecent'))}</button>
      </div>
      <div class="recent-carousel" aria-label="${escapeHtml(t('recentLines'))}">
        ${state.recentLines
          .map((item, index) => `
            <button class="recent-bus-card" data-action="open-recent-item" data-recent-index="${index}">
              <span class="recent-bus-card__icon"><i data-lucide="bus-front"></i></span>
              <span class="recent-bus-card__line">${escapeHtml(item.type === 'stop' ? item.presetLine : item.line)}</span>
              ${item.type === 'stop' ? `<span class="recent-bus-card__station">${escapeHtml(item.stationId)}</span>` : ''}
            </button>
          `)
          .join('')}
      </div>
    </section>
  `;
}

function renderTopFavoritesCarousel() {
  if (!isAuthenticated() || !state.favorites.length) {
    return '';
  }

  const topFavorites = getTopUsedFavorites();
  if (!topFavorites.length) {
    return '';
  }

  return `
    <section class="recent-carousel-section">
      <div class="section-heading">
        <div class="section-label">${escapeHtml(t('topFavorites'))}</div>
      </div>
      <div class="recent-carousel" aria-label="${escapeHtml(t('topFavorites'))}">
        ${topFavorites
          .map((favorite) => {
            const favoriteName = favorite.favoriteName || favorite.name;
            return `
              <button class="recent-bus-card" data-action="open-stop" ${stopDataset(favorite)}>
                <span class="recent-bus-card__icon"><i data-lucide="map-pin"></i></span>
                <span class="recent-bus-card__line">${escapeHtml(favoriteName)}</span>
                <span class="recent-bus-card__station">${escapeHtml(t('stop'))} ${escapeHtml(favorite.stationId)}</span>
              </button>
            `;
          })
          .join('')}
      </div>
    </section>
  `;
}

function renderSheetContent() {
  if (state.activeSheet === 'nearby') {
    return renderStopList(state.nearbyStops, state.nearbyMessage);
  }

  if (state.activeSheet === 'recent-lines') {
    return renderRecentLines();
  }

  if (state.activeSheet === 'departure-lines') {
    const lines = getAvailableArrivalLines();
    if (!lines.length) {
      return renderListMessage(t('noDepartures60'));
    }
    const selected = lines.filter((line) => state.selectedDepartureLines.includes(line));
    const unselected = lines.filter((line) => !state.selectedDepartureLines.includes(line));
    const orderedLines = [...selected, ...unselected];

    return `
      <div class="sheet-controls">
        <button class="ghost-action compact-action" type="button" data-action="select-all-departure-lines">${escapeHtml(t('allLines'))}</button>
        <button class="ghost-action compact-action" type="button" data-action="clear-departure-lines">${escapeHtml(t('clearRecent'))}</button>
      </div>
      <div class="line-filter-bar sheet-line-list">
        ${orderedLines
          .map((line) => `
            <button
              class="line-filter-chip ${state.selectedDepartureLines.includes(line) ? 'active' : ''}"
              data-action="toggle-departure-line"
              data-line="${escapeHtml(line)}"
              aria-pressed="${state.selectedDepartureLines.includes(line) ? 'true' : 'false'}"
            >
              ${escapeHtml(line)}
            </button>
          `)
          .join('')}
      </div>
    `;
  }

  return '';
}

function getSheetTitle() {
  const titles = {
    nearby: t('nearbyStops'),
    'recent-lines': t('recentLines'),
    'departure-lines': t('chooseLines')
  };

  return titles[state.activeSheet] || '';
}

function renderActiveSheet() {
  if (!state.activeSheet) {
    return '';
  }

  return `
    <section class="sheet-backdrop" data-action="close-sheet">
      <div class="bottom-sheet" role="dialog" aria-modal="true" aria-label="${escapeHtml(getSheetTitle())}" tabindex="-1" data-sheet-panel>
        <div class="sheet-header">
          <strong>${escapeHtml(getSheetTitle())}</strong>
          <button class="ghost-action" data-action="close-sheet" type="button">${escapeHtml(t('close'))}</button>
        </div>
        <div class="sheet-body">
          ${renderSheetContent()}
        </div>
      </div>
    </section>
  `;
}

function renderStopChoiceDialog() {
  if (state.favoriteChoiceStop) {
    const stop = state.favoriteChoiceStop;
    const presets = Array.isArray(stop.favorites)
      ? stop.favorites.filter((favorite) => normalizeLineList(favorite.presetLine || ''))
      : [];

    return `
      <section class="choice-dialog-backdrop" data-action="close-dialog">
        <div class="choice-dialog" role="dialog" aria-modal="true" aria-label="${escapeHtml(t('favoritePresetChoice'))}" tabindex="-1" data-dialog-panel>
          <div>
            <div class="hero-block__eyebrow">${escapeHtml(t('favoriteMarker'))}</div>
            <h2>${escapeHtml(stop.name)}</h2>
            <p class="settings-hint">${escapeHtml(t('stop'))} ${escapeHtml(stop.stationId)}</p>
          </div>
          <div class="stack-actions">
            <button class="brutalist-btn" data-action="open-favorite-custom" ${stopDataset({ ...stop, presetLine: '' })}>
              ${escapeHtml(t('customLookup'))}
            </button>
          </div>
          ${
            presets.length
              ? `
                <div class="choice-dialog__lines">
                  <strong>${escapeHtml(t('useFavoritePreset'))}</strong>
                  <div class="map-pick-stop-list">
                    ${presets.map((favorite) => `
                      <button class="map-pick-stop" type="button" data-action="open-favorite-choice-preset" ${stopDataset(favorite)}>
                        <span>
                          <strong>${escapeHtml(favorite.favoriteName || favorite.name)}</strong>
                          <small>${escapeHtml(t('presetLine'))} ${escapeHtml(favorite.presetLine || '')}</small>
                        </span>
                      </button>
                    `).join('')}
                  </div>
                </div>
              `
              : ''
          }
          <div class="stack-actions">
            <button class="ghost-action" data-action="close-favorite-choice">${escapeHtml(t('close'))}</button>
          </div>
        </div>
      </section>
    `;
  }

  if (state.mapPickOptions.length || state.mapPickMessage) {
    return `
      <section class="choice-dialog-backdrop" data-action="close-dialog">
        <div class="choice-dialog" role="dialog" aria-modal="true" aria-label="${escapeHtml(t('chooseNearbyStop'))}" tabindex="-1" data-dialog-panel>
          <div>
            <div class="hero-block__eyebrow">${escapeHtml(t('pickedMapPoint'))}</div>
            <h2>${escapeHtml(t('chooseNearbyStop'))}</h2>
            ${state.mapPickMessage ? `<p class="settings-hint">${escapeHtml(state.mapPickMessage)}</p>` : ''}
          </div>
          <div class="map-pick-stop-list">
            ${state.mapPickOptions.length ? state.mapPickOptions.map((stop, index) => `
              <button class="map-pick-stop" type="button" data-action="select-map-pick-stop" data-stop-index="${index}">
                <span>
                  <strong>${escapeHtml(stop.name)}</strong>
                  <small>${escapeHtml(t('stop'))} ${escapeHtml(stop.stationId)}</small>
                </span>
                ${Number.isFinite(stop.distance) ? `<small>${Math.round(stop.distance)} ${escapeHtml(t('away'))}</small>` : ''}
              </button>
            `).join('') : `<p class="settings-hint">${escapeHtml(t('noStopsArea'))}</p>`}
          </div>
          <div class="stack-actions">
            <button class="ghost-action" data-action="choose-different-stop">${escapeHtml(t('chooseDifferent'))}</button>
          </div>
        </div>
      </section>
    `;
  }

  if (!state.mapPickCandidate) {
    return '';
  }

  const stop = state.mapPickCandidate;
  const lines = stop.lines || [];
  return `
    <section class="choice-dialog-backdrop" data-action="close-dialog">
      <div class="choice-dialog" role="dialog" aria-modal="true" aria-label="${escapeHtml(t('selectedStop'))}" tabindex="-1" data-dialog-panel>
        <div>
          <div class="hero-block__eyebrow">${escapeHtml(t('selectedStop'))}</div>
          <h2>${escapeHtml(stop.name)}</h2>
          <p class="settings-hint">${escapeHtml(t('stop'))} ${escapeHtml(stop.stationId)}</p>
        </div>
        <div class="choice-dialog__lines">
          <strong>${escapeHtml(t('registeredLines'))}</strong>
          ${
            lines.length
              ? `<div class="line-filter-bar">${lines.slice(0, 18).map((line) => `<span class="line-filter-chip">${escapeHtml(line)}</span>`).join('')}</div>`
              : `<p class="settings-hint">${escapeHtml(t('noRegisteredLines'))}</p>`
          }
        </div>
        <div class="stack-actions">
          <button class="ghost-action" data-action="choose-different-stop">${escapeHtml(t('chooseDifferent'))}</button>
          <button class="brutalist-btn" data-action="confirm-picked-stop">${escapeHtml(t('proceed'))}</button>
        </div>
      </div>
    </section>
  `;
}

function renderHomeView() {
  return `
    <section class="home-hero">
      <div class="home-hero__copy">
        <span class="hero-block__eyebrow">${escapeHtml(t('serviceStatus'))}</span>
        <h1 class="home-title">${escapeHtml(t('homeTitle'))}</h1>
        <p class="panel-copy">${escapeHtml(t('homeIntro'))}</p>
      </div>
      <form id="home-search-form" class="home-search-card">
        ${renderField({ id: 'home-search-input', label: t('homeSearchPlaceholder'), icon: 'search', value: state.searchQuery })}
        <button class="brutalist-btn search-submit" type="submit"><i data-lucide="search"></i>${escapeHtml(t('searchButton'))}</button>
      </form>
    </section>

    ${state.mapPickMode ? `
      <section class="home-map-actions">
        <p class="settings-hint">${escapeHtml(t('pickMapHint'))}</p>
      </section>
    ` : ''}

    <section class="map-stage home-map-stage" aria-label="${escapeHtml(t('mapCaption'))}">
      <div class="map-stage__label">
        <span>${escapeHtml(t('mapCaption'))}</span>
        <strong>${escapeHtml(state.nearbyCenterLabel || t('belgradeCenter'))}</strong>
      </div>
    <div id="map-anchor" class="inline-map-anchor"></div>
    </section>

    <section class="home-map-actions home-map-actions--below-map">
      <div class="stack-actions">
        <button class="brutalist-btn outline" type="button" data-action="locate-nearby"><i data-lucide="locate-fixed"></i>${escapeHtml(t('useLocation'))}</button>
        <button class="brutalist-btn outline" type="button" data-action="start-map-pick"><i data-lucide="map-pin"></i>${escapeHtml(t('chooseOnMap'))}</button>
      </div>
    </section>

    <section class="panel-block">
      <button class="nearby-toggle" data-action="toggle-nearby" aria-expanded="${state.nearbyExpanded ? 'true' : 'false'}">
        <span class="nearby-toggle__label">${escapeHtml(t('nearbyStops'))}</span>
        <i class="nearby-toggle__icon" data-lucide="chevron-${state.nearbyExpanded ? 'up' : 'down'}"></i>
      </button>
      ${
        state.nearbyExpanded
          ? `<div class="stop-list nearby-preview">${renderStopList(state.nearbyStops.slice(0, 3), state.nearbyMessage)}</div>`
          : ''
      }
    </section>

    ${renderTopFavoritesCarousel()}
  `;
}

function renderSearchView() {
  return `
    <h1>${escapeHtml(t('searchStops'))}</h1>
    <section class="search-wrap stack-gap">
      <form id="search-form" class="stack-gap">
        ${renderField({ id: 'search-input', label: t('searchPlaceholder'), icon: 'search', value: state.searchQuery })}
        <button class="brutalist-btn search-submit" type="submit"><i data-lucide="search"></i>${escapeHtml(t('search'))}</button>
        <button class="brutalist-btn outline" type="button" data-action="start-map-pick"><i data-lucide="map-pin"></i>${escapeHtml(t('chooseOnMap'))}</button>
      </form>
    </section>
    <section class="panel-block">
      <button class="nearby-toggle" data-action="toggle-search-results" aria-expanded="${state.searchResultsExpanded ? 'true' : 'false'}">
        <span class="nearby-toggle__label">${escapeHtml(t('searchResultsTitle'))}</span>
        <i class="nearby-toggle__icon" data-lucide="chevron-${state.searchResultsExpanded ? 'up' : 'down'}"></i>
      </button>
      ${
        state.searchResultsExpanded
          ? `<div class="stop-list nearby-preview">${renderStopList(state.searchResults, state.searchMessage)}</div>`
          : ''
      }
    </section>
    <div id="map-anchor" class="inline-map-anchor"></div>
  `;
}

function renderNavigateView() {
  const showFromSuggestions = state.navFromLoading || state.navFromSuggestions.length;
  const showToSuggestions = state.navToQuery.trim() && (state.navToLoading || state.navToSuggestions.length);
  return `
    <h1>${escapeHtml(t('navigate'))}</h1>
    <section class="search-wrap stack-gap">
      <form id="navigation-form" class="stack-gap">
        <div class="stack-gap stack-gap--tight">
          ${renderField({ id: 'nav-from-input', label: t('fromStop'), icon: 'map-pin', value: state.navFromQuery, autocomplete: 'off' })}
          <button class="brutalist-btn outline" type="button" data-action="use-nav-location">
            <i data-lucide="locate-fixed"></i>${escapeHtml(t('useCurrentLocationStart'))}
          </button>
          ${
            showFromSuggestions
              ? renderNavSuggestions('from', state.navFromSuggestions, state.navFromLoading ? t('loadingStops') : t('noStopMatches'))
              : ''
          }
        </div>
        <div class="stack-gap stack-gap--tight">
          ${renderField({ id: 'nav-to-input', label: t('toStop'), icon: 'flag', value: state.navToQuery, autocomplete: 'off' })}
          ${
            state.navFromStop || state.navFromLocation
              ? `<p class="settings-hint">${escapeHtml(t('routeSearchDestinationHint'))}</p>`
              : ''
          }
          ${
            showToSuggestions
              ? renderNavSuggestions('to', state.navToSuggestions, state.navToLoading ? t('loadingStops') : t('noConnectedStops'))
              : ''
          }
        </div>
        <p class="settings-hint">${escapeHtml(t('routeSearchHint'))}</p>
        <button class="brutalist-btn" type="submit"><i data-lucide="navigation"></i>${escapeHtml(t('findRoute'))}</button>
      </form>
      <p class="panel-copy">${escapeHtml(state.navMessage)}</p>
      ${state.navFallbackSuggestion ? `
        <button class="brutalist-btn outline" type="button" data-action="use-nav-fallback" ${stopDataset(state.navFallbackSuggestion)}>
          ${escapeHtml(t('useSuggestedStation'))}: ${escapeHtml(formatStationLabel(state.navFallbackSuggestion))}
        </button>
      ` : ''}
    </section>
    <section class="panel-block">
      ${state.navFromStop && state.navToStop ? `
        <div class="route-card nav-summary-card">
          <div class="nav-summary-card__route">
            <span title="${escapeHtml(`${state.navFromStop.name} (${state.navFromStop.stationId})`)}">${escapeHtml(`${state.navFromStop.name} (${state.navFromStop.stationId})`)}</span>
            <i data-lucide="arrow-right"></i>
            <span title="${escapeHtml(`${state.navToStop.name} (${state.navToStop.stationId})`)}">${escapeHtml(`${state.navToStop.name} (${state.navToStop.stationId})`)}</span>
          </div>
          <div class="nav-summary-card__meta">
            <span class="nav-summary-card__stop">
              <i data-lucide="map-pin"></i>
              ${escapeHtml(t('stop'))} ${escapeHtml(state.navFromStop.stationId)}
            </span>
            <i class="nav-summary-card__meta-arrow" data-lucide="arrow-right"></i>
            <span class="nav-summary-card__stop nav-summary-card__stop--end">
              <i data-lucide="flag"></i>
              ${escapeHtml(t('stop'))} ${escapeHtml(state.navToStop.stationId)}
            </span>
          </div>
        </div>
      ` : ''}
      ${renderNavigationResults()}
    </section>
    <div id="map-anchor" class="inline-map-anchor"></div>
  `;
}

function renderFavoritesView() {
  return `
    <h1>${escapeHtml(t('savedStops'))}</h1>
    <section class="stop-list favorites-list">
      ${renderFavoritesList()}
    </section>
  `;
}

function renderProfileView() {
  const username = getCurrentUsername();
  const authBlock = isAuthenticated()
    ? `
      <div class="account-card">
        <div>
          <div class="account-card__label">${escapeHtml(t('signedIn'))}</div>
          <div class="account-card__value">${escapeHtml(username || t('authenticatedUser'))}</div>
        </div>
        <button class="brutalist-btn outline profile-btn" data-action="logout"><i data-lucide="log-out"></i>${escapeHtml(t('logout'))}</button>
      </div>
    `
    : `
      <div class="account-card">
        <div>
          <div class="account-card__label">${escapeHtml(t('account'))}</div>
          <div class="account-card__value">${escapeHtml(t('accountCopy'))}</div>
        </div>
        <div class="stack-actions">
          <button class="brutalist-btn profile-btn" data-action="open-auth" data-mode="login" data-return="profile"><i data-lucide="log-in"></i>${escapeHtml(t('logIn'))}</button>
          <button class="brutalist-btn outline profile-btn" data-action="open-auth" data-mode="register" data-return="profile"><i data-lucide="user-plus"></i>${escapeHtml(t('register'))}</button>
        </div>
      </div>
    `;

  return `
    <h1>${escapeHtml(t('profile'))}</h1>
    <section class="settings-list">
      <div class="settings-item settings-item--stack">
        ${authBlock}
      </div>
      <div class="settings-item">
        <span>${escapeHtml(t('language'))}</span>
        <div class="language-switch" role="group" aria-label="${escapeHtml(t('language'))}">
          <button class="${state.language === 'sr' ? 'active' : ''}" data-action="set-language" data-language="sr">${escapeHtml(t('serbian'))}</button>
          <button class="${state.language === 'en' ? 'active' : ''}" data-action="set-language" data-language="en">${escapeHtml(t('english'))}</button>
        </div>
      </div>
    </section>
  `;
}

function renderAuthView() {
  const isLogin = state.authMode === 'login';
  const isRegister = state.authMode === 'register';
  const isResetRequest = state.authMode === 'reset-request';
  const isResetConfirm = state.authMode === 'reset-confirm';
  const title = isLogin
    ? t('logIn')
    : isRegister
      ? t('register')
      : t('resetPassword');
  const copy = isLogin
    ? t('loginCopy')
    : isRegister
      ? t('registerCopy')
      : isResetRequest
        ? t('resetRequestCopy')
        : t('resetConfirmCopy');
  const formId = isLogin
    ? 'login-form'
    : isRegister
      ? 'register-form'
      : isResetRequest
        ? 'password-reset-request-form'
        : 'password-reset-confirm-form';
  const submitLabel = isLogin
    ? t('logIn')
    : isRegister
      ? t('register')
      : isResetRequest
        ? t('sendReset')
        : t('resetWithToken');
  return `
    <section class="auth-shell">
      <button class="back-link" data-action="return-from-auth">
        <i data-lucide="arrow-left"></i>
        <span>${escapeHtml(t('back'))}</span>
      </button>
      <div class="auth-card">
        <div class="hero-block__eyebrow">${escapeHtml(t('profileAccess'))}</div>
        <h1>${escapeHtml(title)}</h1>
        <p class="panel-copy">${escapeHtml(copy)}</p>
        <form id="${formId}" class="stack-gap">
          <div id="auth-error" class="error-banner" hidden></div>
          ${isResetConfirm ? '' : renderField({ id: 'auth-username', label: t('username'), icon: 'user', autocomplete: 'username' })}
          ${isResetConfirm ? renderField({ id: 'auth-reset-token', label: t('resetToken'), icon: 'key-round', autocomplete: 'one-time-code' }) : ''}
          ${isResetRequest ? '' : renderField({ id: 'auth-password', label: isResetConfirm ? t('newPassword') : t('password'), icon: 'key', type: 'password', autocomplete: isLogin ? 'current-password' : 'new-password' })}
          <button class="brutalist-btn" type="submit">${escapeHtml(submitLabel)}</button>
        </form>
        ${isLogin ? `
          <button class="ghost-action auth-switch" data-action="switch-auth-mode" data-mode="reset-request">${escapeHtml(t('forgotPassword'))}</button>
          <button class="ghost-action auth-switch" data-action="switch-auth-mode" data-mode="register">${escapeHtml(t('needAccount'))}</button>
        ` : ''}
        ${isResetRequest ? `<button class="ghost-action auth-switch" data-action="switch-auth-mode" data-mode="reset-confirm">${escapeHtml(t('resetWithToken'))}</button>` : ''}
        ${!isLogin ? `<button class="ghost-action auth-switch" data-action="switch-auth-mode" data-mode="login">${escapeHtml(isRegister ? t('alreadyRegistered') : t('backToLogin'))}</button>` : ''}
      </div>
    </section>
  `;
}

function renderStopView() {
  if (!state.currentStop) {
    return `
      <section class="auth-shell">
        <div class="auth-card">
          <h1>${escapeHtml(t('stopNotFound'))}</h1>
          <button class="brutalist-btn" data-action="go-back">${escapeHtml(t('goBack'))}</button>
        </div>
      </section>
    `;
  }

  const favorite = getFavoriteForStop(state.currentStop);
  const favoriteAction = isAuthenticated()
    ? favorite
      ? `<button class="ghost-action" data-action="delete-favorite" data-favorite-name="${escapeHtml(favorite.favoriteName)}">${escapeHtml(t('removeFavorite'))}</button>`
      : `
        <form id="favorite-form" class="favorite-form">
          <label class="inline-field" for="favorite-name-input">
            <span>${escapeHtml(t('favoriteLabel'))}</span>
            <input class="inline-input" id="favorite-name-input" type="text" placeholder="${escapeHtml(t('favoriteLabel'))}" value="${escapeHtml(state.currentStop.name)}">
          </label>
          <label class="inline-field" for="favorite-line-input">
            <span>${escapeHtml(t('optionalLine'))}</span>
            <input class="inline-input" id="favorite-line-input" type="text" placeholder="${escapeHtml(t('optionalLine'))}" value="${escapeHtml(state.selectedDepartureLines.join(', '))}">
          </label>
          <button class="favorite-save-btn" type="submit" aria-label="${escapeHtml(t('saveFavorite'))}" title="${escapeHtml(t('saveFavorite'))}">
            <i data-lucide="star"></i>
            <span>${escapeHtml(t('savePreset'))}</span>
          </button>
        </form>
      `
    : `<button class="ghost-action" data-action="open-auth" data-mode="login" data-return="stop">${escapeHtml(t('loginToSave'))}</button>`;

  return `
    <section class="stop-shell">
      <button class="back-link" data-action="go-back">
        <i data-lucide="arrow-left"></i>
        <span>${escapeHtml(t('back'))}</span>
      </button>
      <div class="stop-hero">
        <div class="stop-hero__eyebrow">${escapeHtml(t('stop'))} ${escapeHtml(state.currentStop.stationId)}</div>
        <h1>${escapeHtml(state.currentStop.name)}</h1>
        <div class="stack-actions">${favoriteAction}</div>
      </div>

      <section class="panel-block">
        <div class="panel-block__header">
          <div class="section-label">${escapeHtml(t('upcomingDepartures'))}</div>
          <button class="ghost-action" data-action="reload-stop">${escapeHtml(t('refresh'))}</button>
        </div>
        ${renderArrivals()}
      </section>

      <section class="panel-block">
        <div class="panel-block__header">
          <div class="section-label">${escapeHtml(t('lineRoute'))}</div>
          <span class="panel-copy">${escapeHtml(state.routeLine || t('noLineSelected'))}</span>
        </div>
        ${state.routeDirections.length ? `
          <section class="map-stage route-map-stage" aria-label="${escapeHtml(t('lineRoute'))}">
            <div class="map-stage__label">
              <span>${escapeHtml(t('lineRoute'))}</span>
              <strong>${escapeHtml(state.routeLine || t('line'))}</strong>
            </div>
            <div id="map-anchor" class="inline-map-anchor"></div>
          </section>
        ` : ''}
        ${renderRouteDirections()}
      </section>
    </section>
  `;
}

function renderCurrentView() {
  switch (state.currentView) {
    case 'search':
      return renderSearchView();
    case 'navigate':
      return renderNavigateView();
    case 'favorites':
      return renderFavoritesView();
    case 'profile':
      return renderProfileView();
    case 'auth':
      return renderAuthView();
    case 'stop':
      return renderStopView();
    case 'home':
    default:
      return renderHomeView();
  }
}

function afterRenderBindings() {
  createAppIcons();
  syncShell();
  updateNavLabels();
  setActiveNav();
  updateMapForCurrentView();
  focusActiveOverlay();
}

function getFocusableElements(container) {
  return Array.from(container.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'));
}

function closeTopOverlay() {
  if (state.activeSheet) {
    state.activeSheet = '';
    renderView(state.currentView);
    return true;
  }

  if (state.favoriteChoiceStop || state.mapPickCandidate || state.mapPickOptions.length || state.mapPickMessage) {
    state.favoriteChoiceStop = null;
    state.mapPickCandidate = null;
    state.mapPickOptions = [];
    state.mapPickMessage = '';
    renderView(state.currentView);
    return true;
  }

  return false;
}

function focusActiveOverlay() {
  const dialog = contentArea.querySelector('[role="dialog"]');
  if (!dialog) {
    return;
  }

  const focusables = getFocusableElements(dialog);
  (focusables[0] || dialog).focus({ preventScroll: true });
}

function scrollMainToTop() {
  if (!contentArea) {
    return;
  }

  requestAnimationFrame(() => {
    contentArea.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
  });
}

function renderView(viewName) {
  const viewChanged = state.currentView !== viewName;
  let focusState = null;
  const activeElement = document.activeElement;
  if (
    activeElement
    && (activeElement.id === 'nav-from-input' || activeElement.id === 'nav-to-input')
  ) {
    focusState = {
      id: activeElement.id,
      selectionStart: activeElement.selectionStart,
      selectionEnd: activeElement.selectionEnd
    };
  }

  if (NAV_VIEWS.has(viewName)) {
    state.previousView = viewName;
  }

  state.currentView = viewName;
  contentArea.innerHTML = `${renderCurrentView()}${renderActiveSheet()}${renderStopChoiceDialog()}`;
  afterRenderBindings();

  if (focusState && state.currentView === 'navigate') {
    const nextInput = document.getElementById(focusState.id);
    if (nextInput) {
      nextInput.focus();
      if (
        typeof focusState.selectionStart === 'number'
        && typeof focusState.selectionEnd === 'number'
      ) {
        nextInput.setSelectionRange(focusState.selectionStart, focusState.selectionEnd);
      }
    }
  }

  if (viewChanged && !focusState) {
    scrollMainToTop();
  }
}

function openAuth(mode, returnView = 'profile') {
  state.authMode = mode;
  state.authReturnView = returnView;
  renderView('auth');
}

async function loadFavorites() {
  if (!isAuthenticated()) {
    state.favorites = [];
    state.favoritesMessage = t('favoritesRequireLogin');
    if (state.currentView === 'favorites') {
      renderView('favorites');
    }
    return;
  }

  try {
    const data = await apiRequest('/favorites', { auth: true });
    state.favorites = await Promise.all((data.favorites || []).map(async (favorite) =>
      enrichStopCoordinates(normalizeStop({
        station_id: favorite.station_id,
        name: favorite.name,
        favoriteName: favorite.name,
        presetLine: favorite.line
      }))
    ));
    state.favoritesMessage = state.favorites.length ? '' : t('noFavorites');
  } catch (error) {
    state.favorites = [];
    state.favoritesMessage = error.message;
  }

  if (state.currentView === 'home' || state.currentView === 'favorites' || state.currentView === 'stop') {
    renderView(state.currentView);
  }
}

async function findNearbyStops(options = {}) {
  const { useBrowserLocation = false, coords: providedCoords = null, label: providedLabel = '', openSheet = false, droppedPin = false } = options;
  state.nearbyMessage = t('loadingNearby');
  if (state.currentView === 'home') {
    renderView('home');
  }

  let coords = providedCoords || DEFAULT_CENTER;
  let label = providedLabel || t('belgradeCenter');

  if (!providedCoords && useBrowserLocation && navigator.geolocation) {
    try {
      coords = await new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(
          (position) => resolve({
            lat: position.coords.latitude,
            lon: position.coords.longitude
          }),
          reject,
          { enableHighAccuracy: true, timeout: 8000 }
        );
      });
      label = t('aroundLocation');
    } catch (error) {
      state.nearbyMessage = t('locationFailed');
    }
  }

  try {
    const data = await apiRequest(`/stops/nearby?lat=${coords.lat}&lon=${coords.lon}&radius=650`);
    state.nearbyStops = (data.stops || []).map(normalizeStop);
    state.nearbyCenterLabel = label;
    if (droppedPin && state.currentView === 'search') {
      state.searchResults = state.nearbyStops;
      state.searchMessage = state.searchResults.length
        ? `${state.searchResults.length} ${state.searchResults.length === 1 ? t('resultFound') : t('resultsFound')}`
        : t('noStopsArea');
    }
    if (droppedPin) {
      renderDroppedPin(coords);
    }
    if (!state.nearbyStops.length) {
      state.nearbyMessage = t('noStopsArea');
    }
  } catch (error) {
    state.nearbyStops = [];
    state.nearbyMessage = error.message;
  }

  if (openSheet) {
    state.activeSheet = 'nearby';
  }

  if (state.currentView === 'home') {
    renderView('home');
  } else if (openSheet) {
    renderView(state.currentView);
  }
}

async function searchStops(query) {
  state.searchQuery = query.trim();
  state.searchResults = [];
  state.searchResultsExpanded = false;
  state.searchResolvedAddress = '';
  state.searchMessage = state.searchQuery ? t('searchingStops') : t('enterStop');
  renderView('search');

  if (!state.searchQuery) {
    return;
  }

  try {
    const data = await resolveSearchStops(state.searchQuery, 8);
    state.searchResults = data.stops;
    state.searchResolvedAddress = data.resolvedAddress || '';
    state.searchMessage = state.searchResults.length
      ? `${state.searchResults.length} ${state.searchResults.length === 1 ? t('resultFound') : t('resultsFound')}`
      : t('noStopMatches');
    state.searchResultsExpanded = false;
  } catch (error) {
    state.searchResults = [];
    state.searchResultsExpanded = false;
    state.searchMessage = error.message === 'Address was not found' ? t('addressNotFound') : error.message;
  }

  renderView('search');
}

async function resolveStopForNavigation(query, options = {}) {
  const candidates = await resolveNavigationCandidates(query, options);
  return candidates[0] || null;
}

async function resolveAddressStopCandidates(value, limit = 5, radius = 299) {
  try {
    const data = await apiRequest(`/search/address?q=${encodeURIComponent(value)}&radius=${encodeURIComponent(radius)}`);
    return dedupeStopsByStation((data.stops || [])
      .map(normalizeStop)
      .filter((stop) => stop.distance === null || stop.distance <= radius), limit);
  } catch (error) {
    return [];
  }
}

function normalizeSearchText(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase();
}

function stopGroupKey(stop) {
  return normalizeSearchText(`${stop.name || ''}`).replace(/\s+/g, ' ').trim();
}

function dedupeStopsByName(stops, limit = stops.length) {
  const groups = new Map();
  stops.forEach((stop) => {
    const key = stopGroupKey(stop) || stop.stationId;
    const existing = groups.get(key);
    if (!existing || Number(stop.distance ?? 9999) < Number(existing.distance ?? 9999)) {
      groups.set(key, stop);
    }
  });
  return Array.from(groups.values()).slice(0, limit);
}

function dedupeStopsByStation(stops, limit = stops.length) {
  const groups = new Map();
  stops.forEach((stop) => {
    const key = stop.stationId || stop.rawStopId;
    if (!key) {
      return;
    }
    const existing = groups.get(key);
    if (!existing || Number(stop.distance ?? 9999) < Number(existing.distance ?? 9999)) {
      groups.set(key, stop);
    }
  });
  return Array.from(groups.values())
    .sort((left, right) => Number(left.distance ?? 9999) - Number(right.distance ?? 9999))
    .slice(0, limit);
}

function groupNavigationStops(stops, limit = 5) {
  const groups = new Map();
  stops.forEach((stop) => {
    const normalizedStop = normalizeStop(stop);
    const key = stopGroupKey(normalizedStop) || normalizedStop.stationId;
    if (!key) {
      return;
    }

    if (!groups.has(key)) {
      groups.set(key, {
        ...normalizedStop,
        isGroup: true,
        groupKey: key,
        platforms: [],
        stationIds: [],
        lines: []
      });
    }

    const group = groups.get(key);
    if (!group.platforms.some((platform) => platform.stationId === normalizedStop.stationId)) {
      group.platforms.push({
        ...normalizedStop,
        sharedLines: Array.isArray(stop.sharedLines) ? stop.sharedLines : Array.isArray(stop.shared_lines) ? stop.shared_lines : []
      });
    }
  });

  return Array.from(groups.values()).map((group) => {
    group.platforms.sort((left, right) => Number(left.distance ?? 9999) - Number(right.distance ?? 9999));
    group.stationIds = group.platforms.map((platform) => platform.stationId).filter(Boolean);
    group.lines = Array.from(new Set(group.platforms.flatMap((platform) => platform.lines || []))).sort(compareLineLabels);
    return group;
  }).slice(0, limit);
}

function findSuggestionByGroup(type, groupKey) {
  const suggestions = type === 'from' ? state.navFromSuggestions : state.navToSuggestions;
  return suggestions.find((suggestion) => suggestion.isGroup && suggestion.groupKey === groupKey) || null;
}

async function resolveSearchStops(query, limit = 8) {
  const value = String(query || '').trim();
  if (!value) {
    return {
      stops: [],
      resolvedAddress: ''
    };
  }

  if (/^\d+$/.test(value)) {
    try {
      const data = await apiRequest(`/stops?station_id=${encodeURIComponent(value)}`);
      const stops = (data.stops || []).map(normalizeStop);
      if (stops.length) {
        return {
          stops: stops.slice(0, limit),
          resolvedAddress: ''
        };
      }
    } catch (error) {
      // Fall through to regular search.
    }
  }

  if (looksLikeStreetAddress(value)) {
    try {
      const data = await apiRequest(`/search/address?q=${encodeURIComponent(value)}&radius=650`);
      const stops = dedupeStopsByName((data.stops || [])
        .map(normalizeStop)
        .filter((stop) => stop.distance === null || stop.distance <= 650), limit);
      if (stops.length) {
        return {
          stops,
          resolvedAddress: data.resolved_address || ''
        };
      }
    } catch (error) {
      // A number can also be a station id or part of a stop name.
    }
  }

  try {
    const data = await apiRequest(`/search?q=${encodeURIComponent(value)}`);
    return {
      stops: (data.matches || []).map(normalizeStop).slice(0, limit),
      resolvedAddress: ''
    };
  } catch (error) {
    return {
      stops: [],
      resolvedAddress: ''
    };
  }
}

function sharedLineCount(leftStop, rightStop) {
  if (Array.isArray(leftStop?.sharedLines) && leftStop.sharedLines.length) {
    return leftStop.sharedLines.length;
  }

  const leftLines = new Set((leftStop?.lines || []).map(String));
  if (!leftLines.size) {
    return 0;
  }

  return (rightStop?.lines || []).filter((line) => leftLines.has(String(line))).length;
}

function getSharedLines(leftStop, rightStop) {
  if (Array.isArray(leftStop?.sharedLines) && leftStop.sharedLines.length) {
    return leftStop.sharedLines.map(String).sort(compareLineLabels);
  }

  const rightLines = new Set((rightStop?.lines || []).map(String));
  return (leftStop?.lines || [])
    .map(String)
    .filter((line) => rightLines.has(line))
    .sort(compareLineLabels);
}

function rankNavigationSuggestions(stops, referenceStop = null) {
  return [...stops].sort((left, right) => {
    if (referenceStop) {
      const sharedDiff = sharedLineCount(right, referenceStop) - sharedLineCount(left, referenceStop);
      if (sharedDiff !== 0) {
        return sharedDiff;
      }
    } else {
      const lineDiff = (right.lines || []).length - (left.lines || []).length;
      if (lineDiff !== 0) {
        return lineDiff;
      }
    }

    return Number(left.distance ?? 9999) - Number(right.distance ?? 9999);
  });
}

async function resolveNavigationCandidates(query, options = {}) {
  const value = String(query || '').trim();
  if (!value) {
    return [];
  }

  const { connectedFromStationId = '', limit = 5, preferAddress = false, addressRadius = 299 } = options;
  const labeledStationId = extractStationIdFromLabel(value);

  if (labeledStationId) {
    const data = await apiRequest(`/stops?station_id=${encodeURIComponent(labeledStationId)}`);
    const matches = (data.stops || []).map(normalizeStop);
    return matches.slice(0, 1);
  }

  if (/^\d+$/.test(value)) {
    try {
      const data = await apiRequest(`/stops?station_id=${encodeURIComponent(value)}`);
      const matches = (data.stops || []).map(normalizeStop);
      if (matches.length) {
        return matches.slice(0, 1);
      }
    } catch (error) {
      // Fall through to connected/address/station-name resolution below.
    }
  }

  if (preferAddress && looksLikeStreetAddress(value)) {
    const addressMatches = await resolveAddressStopCandidates(value, limit, addressRadius);
    if (addressMatches.length) {
      return addressMatches;
    }
  }

  const mergedMatches = [];
  const seenMatchIds = new Set();
  const addMatches = (matches) => {
    matches.forEach((stop) => {
      const normalizedStop = normalizeStop(stop);
      const key = normalizedStop.stationId || normalizedStop.rawStopId || normalizedStop.name;
      if (!key || seenMatchIds.has(key)) {
        return;
      }
      seenMatchIds.add(key);
      mergedMatches.push({
        ...normalizedStop,
        sharedLines: Array.isArray(stop.sharedLines)
          ? stop.sharedLines
          : Array.isArray(stop.shared_lines)
            ? stop.shared_lines
            : []
      });
    });
  };

  if (connectedFromStationId) {
    try {
      const data = await apiRequest(`/stops/connected?from=${encodeURIComponent(connectedFromStationId)}&q=${encodeURIComponent(value)}`);
      addMatches(data.stops || []);
    } catch (error) {
      // Fall through to address/station resolution below.
    }
  }

  try {
    const data = await apiRequest(`/search?q=${encodeURIComponent(value)}`);
    addMatches(data.matches || []);
  } catch (error) {
    // Fall through to address resolution below.
  }

  if (mergedMatches.length) {
    return mergedMatches.slice(0, limit);
  }

  return looksLikeStreetAddress(value) ? resolveAddressStopCandidates(value, limit, addressRadius) : [];
}

async function loadNavigationStartSuggestions(query, requestId = ++navFromRequestId) {
  if (requestId !== navFromRequestId) {
    return;
  }

  const rawValue = String(query || '');
  const value = rawValue.trim();
  state.navFromQuery = rawValue;
  state.navFromStop = null;
  state.navFromSelection = null;
  state.navFromLocation = null;
  state.navFromLocationCandidates = [];
  state.navFromLocationAllCandidates = [];
  state.navMessage = value ? t('chooseBothStops') : t('chooseStartFirst');
  state.navRoutes = [];
  state.navDepartures = [];
  state.navExpandedRouteKeys = new Set();
  state.navFallbackSuggestion = null;

  if (!value) {
    state.navFromSuggestions = [];
    state.navFromLoading = false;
    renderView('navigate');
    return;
  }

  if (navFromSuggestionsCache.has(value)) {
    if (requestId !== navFromRequestId) {
      return;
    }
    state.navFromSuggestions = navFromSuggestionsCache.get(value);
    state.navFromLoading = false;
    renderView('navigate');
    return;
  }

  state.navFromLoading = true;

  try {
    const candidates = await resolveNavigationCandidates(value, {
      limit: 5,
      preferAddress: true,
      addressRadius: 650
    });
    const suggestions = looksLikeStreetAddress(value) || /^\d+$/.test(value)
      ? candidates
      : groupNavigationStops(rankNavigationSuggestions(candidates), 5);
    if (requestId !== navFromRequestId) {
      return;
    }
    state.navFromSuggestions = suggestions.slice(0, 5);
    navFromSuggestionsCache.set(value, state.navFromSuggestions);
  } catch (error) {
    if (requestId !== navFromRequestId) {
      return;
    }
    state.navFromSuggestions = [];
    state.navMessage = error.message;
  } finally {
    if (requestId === navFromRequestId) {
      state.navFromLoading = false;
      renderView('navigate');
    }
  }
}

async function loadNavigationDestinationSuggestions(query, requestId = ++navToRequestId) {
  if (requestId !== navToRequestId) {
    return;
  }

  const rawValue = String(query || '');
  const value = rawValue.trim();
  state.navToQuery = rawValue;
  state.navToStop = null;
  state.navToSelection = null;
  state.navMessage = t('chooseBothStops');
  const usingCurrentLocation = state.navFromLocation
    && state.navFromLocationCandidates.length
    && state.navFromQuery.trim() === t('currentLocationStart');

  if (!state.navFromStop && !state.navFromSelection && !usingCurrentLocation) {
    const fromValue = state.navFromQuery.trim();
    if (!fromValue) {
      state.navToSuggestions = [];
      state.navToLoading = false;
      state.navMessage = t('chooseStartFirst');
      renderView('navigate');
      return;
    }

    try {
      const fromCandidates = await resolveNavigationCandidates(fromValue, {
        limit: 1,
        preferAddress: true,
        addressRadius: 350
      });
      if (requestId !== navToRequestId) {
        return;
      }
      state.navFromStop = fromCandidates[0] ? await getStopDetails(fromCandidates[0]) : null;
      if (state.navFromStop) {
        state.navFromSelection = { mode: 'exact', stop: state.navFromStop };
        state.navFromQuery = formatStationLabel(state.navFromStop);
      }
    } catch (error) {
      state.navFromStop = null;
    }

    if (!state.navFromStop) {
      state.navToSuggestions = [];
      state.navToLoading = false;
      state.navMessage = t('chooseStartFirst');
      renderView('navigate');
      return;
    }
  }

  if (!value) {
    state.navToSuggestions = [];
    state.navToLoading = false;
    renderView('navigate');
    return;
  }

  const referenceStop = usingCurrentLocation ? null : state.navFromStop;
  const cacheKey = `${usingCurrentLocation ? 'current-location' : state.navFromStop?.stationId || 'any'}|${value}`;
  if (navToSuggestionsCache.has(cacheKey)) {
    if (requestId !== navToRequestId) {
      return;
    }
    state.navToSuggestions = navToSuggestionsCache.get(cacheKey);
    state.navToLoading = false;
    renderView('navigate');
    return;
  }

  state.navToLoading = true;

  try {
    const candidates = await resolveNavigationCandidates(value, {
      connectedFromStationId: referenceStop?.stationId || '',
      limit: looksLikeStreetAddress(value) ? 50 : 25,
      preferAddress: true,
      addressRadius: 650
    });
    if (requestId !== navToRequestId) {
      return;
    }
    state.navRoutes = [];
    state.navDepartures = [];
    state.navExpandedRouteKeys = new Set();
    const rankedSuggestions = rankNavigationSuggestions(candidates.map((stop) => ({
      ...normalizeStop(stop),
      sharedLines: Array.isArray(stop.sharedLines)
        ? stop.sharedLines
        : Array.isArray(stop.shared_lines)
          ? stop.shared_lines
          : []
    })).map((stop) => ({
      ...stop,
      sharedLines: referenceStop ? getSharedLines(stop, referenceStop) : stop.sharedLines
    })), referenceStop);

    state.navToSuggestions = looksLikeStreetAddress(value) || /^\d+$/.test(value)
      ? dedupeStopsByStation(rankedSuggestions, 5)
      : groupNavigationStops(rankedSuggestions, 5);
    navToSuggestionsCache.set(cacheKey, state.navToSuggestions);
  } catch (error) {
    if (requestId !== navToRequestId) {
      return;
    }
    state.navToSuggestions = [];
    state.navMessage = error.message;
  } finally {
    if (requestId === navToRequestId) {
      state.navToLoading = false;
      renderView('navigate');
    }
  }
}

async function selectNavigationStart(stop) {
  clearTimeout(navFromInputTimer);
  navFromRequestId += 1;
  state.navFromLocation = null;
  state.navFromLocationCandidates = [];
  state.navFromLocationAllCandidates = [];
  state.navFromStop = await getStopDetails(normalizeStop(stop));
  state.navFromSelection = { mode: 'exact', stop: state.navFromStop };
  state.navFromQuery = formatStationLabel(state.navFromStop);
  state.navFromSuggestions = [];
  state.navToSuggestions = [];
  state.navRoutes = [];
  state.navDepartures = [];
  state.navExpandedRouteKeys = new Set();
  state.navMessage = t('chooseBothStops');
  renderView('navigate');
}

async function selectNavigationStartGroup(group) {
  clearTimeout(navFromInputTimer);
  navFromRequestId += 1;
  state.navFromLocation = null;
  state.navFromLocationCandidates = [];
  state.navFromLocationAllCandidates = [];
  state.navFromStop = null;
  state.navFromSelection = { mode: 'flexible', name: group.name, platforms: group.platforms || [] };
  state.navFromQuery = group.name;
  state.navFromSuggestions = [];
  state.navToSuggestions = [];
  state.navRoutes = [];
  state.navDepartures = [];
  state.navExpandedRouteKeys = new Set();
  state.navMessage = t('chooseBothStops');
  renderView('navigate');
}

async function selectNavigationDestination(stop) {
  clearTimeout(navToInputTimer);
  navToRequestId += 1;
  state.navToStop = await getStopDetails(normalizeStop(stop));
  state.navToSelection = { mode: 'exact', stop: state.navToStop };
  state.navToQuery = formatStationLabel(state.navToStop);
  state.navToSuggestions = [];
  state.navRoutes = [];
  state.navDepartures = [];
  state.navExpandedRouteKeys = new Set();
  state.navMessage = t('chooseBothStops');
  renderView('navigate');
}

async function selectNavigationDestinationGroup(group) {
  clearTimeout(navToInputTimer);
  navToRequestId += 1;
  state.navToStop = null;
  state.navToSelection = { mode: 'flexible', name: group.name, platforms: group.platforms || [] };
  state.navToQuery = group.name;
  state.navToSuggestions = [];
  state.navRoutes = [];
  state.navDepartures = [];
  state.navExpandedRouteKeys = new Set();
  state.navMessage = t('chooseBothStops');
  renderView('navigate');
}

function getBrowserPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error(t('locationFailed')));
      return;
    }

    navigator.geolocation.getCurrentPosition(
      (position) => resolve({
        lat: position.coords.latitude,
        lon: position.coords.longitude
      }),
      () => reject(new Error(t('locationFailed'))),
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 60000 }
    );
  });
}

async function useCurrentLocationAsNavigationStart() {
  state.navFromLoading = true;
  state.navFromSuggestions = [];
  state.navRoutes = [];
  state.navDepartures = [];
  state.navExpandedRouteKeys = new Set();
  state.navMessage = t('loadingNearby');
  renderView('navigate');

  try {
    const coords = await getBrowserPosition();
    const data = await apiRequest(`/stops/nearby?lat=${coords.lat}&lon=${coords.lon}&radius=650`);
    const candidates = (data.stops || []).map(normalizeStop);
    if (!candidates.length) {
      state.navMessage = t('noStopsArea');
      state.navFromLoading = false;
      renderView('navigate');
      return;
    }

    state.navFromLocation = coords;
    state.navFromLocationCandidates = candidates.slice(0, NAV_LOCATION_INITIAL_LIMIT);
    state.navFromLocationAllCandidates = candidates.slice(0, NAV_LOCATION_RETRY_LIMIT);
    state.navFromStop = null;
    state.navFromSelection = { mode: 'location' };
    state.navFromQuery = t('currentLocationStart');
    state.navFromSuggestions = [];
    state.navToSuggestions = [];
    state.navMessage = t('chooseBothStops');
  } catch (error) {
    state.navFromLocation = null;
    state.navFromLocationCandidates = [];
    state.navFromLocationAllCandidates = [];
    state.navFromStop = null;
    state.navMessage = error.message;
  } finally {
    state.navFromLoading = false;
    renderView('navigate');
  }
}

function scoreNavigationRoute(route, fromStop, toStop) {
  const transferPenalty = route.type === 'direct' ? 0 : 1000;
  const stopsCount = Number(route.stops_count || 0);
  const walkingDistance = Number(fromStop.distance || 0) + Number(toStop.distance || 0);
  const lineChoiceBoost = Math.min((fromStop.lines || []).length + (toStop.lines || []).length, 18) * 0.08;
  return transferPenalty + stopsCount + (walkingDistance / 90) - lineChoiceBoost;
}

function routeWalkingDistance(route) {
  const originWalk = Number(route.origin_walk_m ?? route.from_stop_distance ?? 0);
  const destinationWalk = Number(route.destination_walk_m ?? route.to_stop_distance ?? 0);
  return {
    origin: Number.isFinite(originWalk) ? originWalk : 0,
    destination: Number.isFinite(destinationWalk) ? destinationWalk : 0
  };
}

function navigationPriorityBucket(route) {
  const walking = routeWalkingDistance(route);
  if (route.priority_bucket !== undefined && route.priority_bucket !== null) {
    const priority = Number(route.priority_bucket);
    if (Number.isFinite(priority)) {
      return priority;
    }
  }

  if (route.type === 'direct' && walking.origin <= 1250) {
    return 0;
  }

  if (route.type === 'direct') {
    return 1;
  }

  return 3;
}

function compareNavigationRoutes(left, right) {
  const bucketDiff = navigationPriorityBucket(left) - navigationPriorityBucket(right);
  if (bucketDiff !== 0) {
    return bucketDiff;
  }

  const scoreDiff = Number(left.route_score || 0) - Number(right.route_score || 0);
  if (scoreDiff !== 0) {
    return scoreDiff;
  }

  const stopsDiff = Number(left.stops_count || 0) - Number(right.stops_count || 0);
  if (stopsDiff !== 0) {
    return stopsDiff;
  }

  const leftLine = left.type === 'direct' ? left.line : left.line1;
  const rightLine = right.type === 'direct' ? right.line : right.line1;
  return compareLineLabels(leftLine, rightLine);
}

function sortNavigationRoutes(routes, fromStop, toStop) {
  return [...routes].sort((left, right) =>
    compareNavigationRoutes(
      { ...left, route_score: scoreNavigationRoute(left, fromStop, toStop) },
      { ...right, route_score: scoreNavigationRoute(right, fromStop, toStop) }
    )
  );
}

function getSelectedDestinationDisplayStop(selectedStop, selection, fallbackStop = null) {
  if (selectedStop?.stationId) {
    return selectedStop;
  }

  if (selection?.mode === 'exact' && selection.stop?.stationId) {
    return selection.stop;
  }

  if (selection?.mode === 'flexible') {
    const platform = (selection.platforms || []).find((stop) => stop?.stationId) || fallbackStop;
    if (platform?.stationId) {
      return {
        ...platform,
        name: selection.name || platform.name
      };
    }
  }

  return fallbackStop;
}

function applySelectedDestinationDisplay(routes, destinationStop) {
  if (!destinationStop?.stationId) {
    return routes;
  }

  return routes.map((route) => {
    const routeDestinationId = String(route.dest_station_id || route.to_station_id || '');
    const selectedDestinationId = String(destinationStop.stationId || '');
    if (routeDestinationId && routeDestinationId !== selectedDestinationId) {
      return {
        ...route,
        requested_to_station_name: destinationStop.name,
        requested_to_station_id: destinationStop.stationId
      };
    }

    return {
      ...route,
      to_station_name: destinationStop.name,
      to_station_id: destinationStop.stationId,
      requested_to_station_name: destinationStop.name,
      requested_to_station_id: destinationStop.stationId,
      to_stop_lat: destinationStop.lat,
      to_stop_lon: destinationStop.lon,
      to_stop_distance: destinationStop.distance,
      to_stop_line_count: (destinationStop.lines || []).length
    };
  });
}

function navigationRouteDedupeKey(route, collapseNearbyOrigins = false) {
  const lineKey = route.type === 'direct' ? route.line : [route.line1, route.line2, route.line3].filter(Boolean).join('>');
  if (route.type === 'multi_transfer') {
    return [
      route.type,
      [route.line1, route.line2, route.line3].filter(Boolean).join('>'),
      [route.transfer1_from_station_id, route.transfer1_to_station_id, route.transfer2_from_station_id, route.transfer2_to_station_id].filter(Boolean).join('>'),
      collapseNearbyOrigins ? '' : route.origin_station_id || route.from_station_id,
      route.dest_station_id || route.to_station_id
    ].join('|');
  }

  if (collapseNearbyOrigins) {
    return [
      route.type,
      lineKey
    ].join('|');
  }

  return [
    route.type,
    lineKey,
    route.origin_station_id || route.from_station_id,
    isTransferRoute(route) ? [getRouteTransferStationId(route), route.transfer1_to_station_id, route.transfer2_to_station_id].filter(Boolean).join('>') : '',
    route.dest_station_id || route.to_station_id
  ].join('|');
}

function navigationRouteGroupKey(route, collapseNearbyOrigins = false) {
  if (route.type === 'direct') {
    return [
      'direct',
      route.line,
      collapseNearbyOrigins ? '' : route.origin_station_id || route.from_station_id,
      route.dest_station_id || route.to_station_id
    ].join('|');
  }

  if (route.type === 'multi_transfer') {
    return [
      'multi_transfer',
      [route.line2, route.line3].filter(Boolean).join('>'),
      [route.transfer1_from_station_id, route.transfer1_to_station_id, route.transfer2_from_station_id, route.transfer2_to_station_id].filter(Boolean).join('>'),
      collapseNearbyOrigins ? '' : route.origin_station_id || route.from_station_id,
      route.dest_station_id || route.to_station_id
    ].join('|');
  }

  return [
    route.type,
    [route.line1, route.line2, route.line3].filter(Boolean).join('>'),
    isTransferRoute(route) ? [getRouteTransferStationId(route), route.transfer1_to_station_id, route.transfer2_to_station_id].filter(Boolean).join('>') : '',
    collapseNearbyOrigins ? '' : route.origin_station_id || route.from_station_id,
    route.dest_station_id || route.to_station_id
  ].join('|');
}

function groupNavigationRoutes(routes, collapseNearbyOrigins = false) {
  const groups = new Map();

  routes.forEach((route) => {
    const key = navigationRouteGroupKey(route, collapseNearbyOrigins);
    if (!groups.has(key)) {
      groups.set(key, {
        ...route,
        firstLineOptions: route.type === 'multi_transfer'
          ? [{ line1: route.line1, route }]
          : [],
        transferOptions: route.type === 'transfer'
          ? [{ line2: route.line2, route }]
          : []
      });
      return;
    }

    const group = groups.get(key);
    if (route.type === 'transfer' && route.line2 && !group.transferOptions.some((option) => option.line2 === route.line2)) {
      group.transferOptions.push({ line2: route.line2, route });
      group.transferOptions.sort((left, right) => compareLineLabels(left.line2, right.line2));
    }

    if (route.type === 'multi_transfer' && route.line1 && !group.firstLineOptions.some((option) => option.line1 === route.line1)) {
      group.firstLineOptions.push({ line1: route.line1, route });
      group.firstLineOptions.sort((left, right) => compareLineLabels(left.line1, right.line1));
    }

    if (Number(route.route_score || 0) < Number(group.route_score || 0)) {
      Object.assign(group, {
        ...route,
        firstLineOptions: group.firstLineOptions,
        transferOptions: group.transferOptions
      });
    }
  });

  return Array.from(groups.values());
}

async function getNavigationStopCandidates(query, selectedStop, options = {}) {
  const rawQuery = String(query || '').trim();
  if (selectedStop && rawQuery === formatStationLabel(selectedStop)) {
    return [await getStopDetails(selectedStop)];
  }

  const candidates = await resolveNavigationCandidates(rawQuery, {
    connectedFromStationId: options.referenceStop?.stationId || '',
    limit: options.candidateLimit || options.limit || 6,
    preferAddress: true,
    addressRadius: options.addressRadius || 650
  });
  const referenceStop = options.referenceStop || null;

  const seen = new Set();
  return candidates
    .map((stop) => {
      const normalizedStop = normalizeStop(stop);
      const sharedLines = Array.isArray(stop.sharedLines)
        ? stop.sharedLines
        : Array.isArray(stop.shared_lines)
          ? stop.shared_lines
          : [];
      return {
        ...normalizedStop,
        sharedLines: sharedLines.length ? sharedLines.map(String) : getSharedLines(normalizedStop, referenceStop)
      };
    })
    .filter((stop) => {
      if (!stop.stationId || seen.has(stop.stationId)) {
        return false;
      }
      seen.add(stop.stationId);
      return true;
    })
    .slice(0, options.limit || 6);
}

async function expandDestinationCandidatesForNavigation(toCandidates, options = {}) {
  const radius = options.radius || 260;
  const limit = options.limit || 8;
  const seedStops = toCandidates
    .filter((stop) => hasCoordinates(stop))
    .slice(0, options.seedLimit || 3);

  if (!seedStops.length) {
    return toCandidates;
  }

  const expandedStops = [...toCandidates];
  const seen = new Set(toCandidates.map((stop) => stop.stationId || stop.rawStopId).filter(Boolean));

  for (const seedStop of seedStops) {
    try {
      const data = await apiRequest(`/stops/nearby?lat=${seedStop.lat}&lon=${seedStop.lon}&radius=${radius}`);
      (data.stops || []).map(normalizeStop).forEach((nearbyStop) => {
        const key = nearbyStop.stationId || nearbyStop.rawStopId;
        if (!key || seen.has(key)) {
          return;
        }

        seen.add(key);
        expandedStops.push({
          ...nearbyStop,
          distance: distanceMeters(seedStop, nearbyStop) ?? nearbyStop.distance
        });
      });
    } catch (error) {
      // Keep the originally resolved destination candidates if nearby expansion fails.
    }
  }

  return rankNavigationSuggestions(expandedStops)
    .slice(0, limit);
}

async function findRoutesForStopPair(fromStop, toStop, options = {}) {
  if (!fromStop?.stationId || !toStop?.stationId || fromStop.stationId === toStop.stationId) {
    return [];
  }

  try {
    const strictParam = options.strictStops ? '&strict_stops=true' : '';
    const routing = await apiRequest(`/routing?from=${encodeURIComponent(fromStop.stationId)}&to=${encodeURIComponent(toStop.stationId)}${strictParam}`);
    return (routing.possible_routes || []).map((route) => ({
      ...route,
      from_station_name: route.from_station_name || (route.origin_station_id && route.origin_station_id !== fromStop.stationId ? '' : fromStop.name),
      from_station_id: route.origin_station_id || fromStop.stationId,
      requested_from_station_name: fromStop.name,
      requested_from_station_id: fromStop.stationId,
      from_stop_lat: route.from_stop_lat ?? fromStop.lat,
      from_stop_lon: route.from_stop_lon ?? fromStop.lon,
      from_stop_distance: route.from_stop_distance ?? route.origin_walk_m ?? fromStop.distance,
      from_stop_line_count: route.from_stop_line_count ?? (fromStop.lines || []).length,
      to_station_name: route.to_station_name || toStop.name,
      to_station_id: route.to_station_id || route.dest_station_id || toStop.stationId,
      requested_to_station_name: toStop.name,
      requested_to_station_id: toStop.stationId,
      to_stop_lat: route.to_stop_lat ?? toStop.lat,
      to_stop_lon: route.to_stop_lon ?? toStop.lon,
      to_stop_distance: route.to_stop_distance ?? route.destination_walk_m ?? toStop.distance,
      to_stop_line_count: route.to_stop_line_count ?? (toStop.lines || []).length,
      route_score: route.route_score ?? scoreNavigationRoute(route, fromStop, toStop)
    }));
  } catch (error) {
    return [];
  }
}

async function findRoutesForStopPairs(fromCandidates, toCandidates, options = {}) {
  const pairs = fromCandidates.flatMap((fromStop) =>
    toCandidates
      .filter((toStop) => fromStop?.stationId && toStop?.stationId && fromStop.stationId !== toStop.stationId)
      .map((toStop) => ({
        from: fromStop.stationId,
        to: toStop.stationId
      }))
  );

  if (!pairs.length) {
    return [];
  }

  const fromByStationId = new Map(fromCandidates.map((stop) => [stop.stationId, stop]));
  const toByStationId = new Map(toCandidates.map((stop) => [stop.stationId, stop]));

  try {
    const routing = await apiRequest('/routing/batch', {
      method: 'POST',
      body: {
        pairs,
        strict_stops: Boolean(options.strictStops)
      }
    });

    return (routing.results || []).flatMap((result) => {
      const fromStop = fromByStationId.get(String(result.from || ''));
      const toStop = toByStationId.get(String(result.to || ''));
      if (!fromStop || !toStop) {
        return [];
      }

      return (result.possible_routes || []).map((route) => {
        const actualFromStop = fromByStationId.get(String(route.origin_station_id || '')) || fromStop;
        const actualToStop = toByStationId.get(String(route.dest_station_id || '')) || toStop;
        return {
          ...route,
          from_station_name: route.from_station_name || (route.origin_station_id && route.origin_station_id !== actualFromStop.stationId ? '' : actualFromStop.name),
          from_station_id: route.origin_station_id || actualFromStop.stationId,
          requested_from_station_name: fromStop.name,
          requested_from_station_id: fromStop.stationId,
          from_stop_lat: route.from_stop_lat ?? actualFromStop.lat,
          from_stop_lon: route.from_stop_lon ?? actualFromStop.lon,
          from_stop_distance: route.from_stop_distance ?? route.origin_walk_m ?? actualFromStop.distance,
          from_stop_line_count: route.from_stop_line_count ?? (actualFromStop.lines || []).length,
          to_station_name: route.to_station_name || toStop.name,
          to_station_id: route.to_station_id || route.dest_station_id || toStop.stationId,
          requested_to_station_name: toStop.name,
          requested_to_station_id: toStop.stationId,
          to_stop_lat: route.to_stop_lat ?? actualToStop.lat,
          to_stop_lon: route.to_stop_lon ?? actualToStop.lon,
          to_stop_distance: route.to_stop_distance ?? route.destination_walk_m ?? actualToStop.distance,
          to_stop_line_count: route.to_stop_line_count ?? (actualToStop.lines || []).length,
          route_score: route.route_score ?? scoreNavigationRoute(route, actualFromStop, actualToStop)
        };
      });
    });
  } catch (error) {
    const routeGroups = await Promise.all(fromCandidates.flatMap((fromStop) =>
      toCandidates.map((toStop) => findRoutesForStopPair(fromStop, toStop, options))
    ));
    return routeGroups.flat();
  }
}

async function findJourneyRoutesFromCurrentLocation(destinationStop) {
  if (!state.navFromLocation || !destinationStop?.stationId) {
    return {
      routes: [],
      fromCandidates: [],
      toCandidates: []
    };
  }

  const journey = await apiRequest('/journey', {
    method: 'POST',
    body: {
      origin: {
        lat: state.navFromLocation.lat,
        lon: state.navFromLocation.lon
      },
      destination: {
        station_id: destinationStop.stationId,
        lat: destinationStop.lat,
        lon: destinationStop.lon
      },
      origin_radius: 1250,
      destination_radius: 350
    }
  });

  return {
    routes: (journey.journeys || []).map((route) => ({
      ...route,
      requested_to_station_name: destinationStop.name,
      requested_to_station_id: destinationStop.stationId
    })),
    fromCandidates: (journey.origin_candidates || []).map(normalizeStop),
    toCandidates: (journey.destination_candidates || []).map(normalizeStop)
  };
}

async function findExactPlatformFallback(fromStop, toCandidates) {
  if (!fromStop?.name || !toCandidates.length) {
    return null;
  }

  try {
    const data = await apiRequest(`/search?q=${encodeURIComponent(fromStop.name)}`);
    const sameGroupCandidates = (data.matches || [])
      .map(normalizeStop)
      .filter((candidate) =>
        candidate.stationId
        && candidate.stationId !== fromStop.stationId
        && stopGroupKey(candidate) === stopGroupKey(fromStop)
      )
      .slice(0, 12);
    if (!sameGroupCandidates.length) {
      return null;
    }

    const routes = await findRoutesForStopPairs(sameGroupCandidates, toCandidates, { strictStops: true });
    const bestRoute = routes.sort(compareNavigationRoutes)[0];
    if (!bestRoute) {
      return null;
    }

    return sameGroupCandidates.find((candidate) => candidate.stationId === bestRoute.from_station_id) || sameGroupCandidates[0];
  } catch (error) {
    return null;
  }
}

async function findNavigationRoutes(fromQuery, toQuery) {
  const selectedFromStop = state.navFromStop;
  const selectedToStop = state.navToStop;
  const rawFromQuery = String(fromQuery || '');
  const rawToQuery = String(toQuery || '');
  state.navFromQuery = rawFromQuery;
  state.navToQuery = rawToQuery;
  state.navRoutes = [];
  state.navDepartures = [];
  state.navExpandedRouteKeys = new Set();

  if (!rawFromQuery.trim() || !rawToQuery.trim()) {
    state.navFromStop = selectedFromStop || null;
    state.navToStop = selectedToStop || null;
    state.navMessage = t('chooseBothStops');
    renderView('navigate');
    return;
  }

  state.navMessage = t('resolvingStops');
  renderView('navigate');

  try {
    const usingCurrentLocation = state.navFromLocation
      && state.navFromLocationCandidates.length
      && rawFromQuery.trim() === t('currentLocationStart');
    let fromCandidates = usingCurrentLocation
      ? state.navFromLocationCandidates
      : state.navFromSelection?.mode === 'flexible'
        ? state.navFromSelection.platforms
        : await getNavigationStopCandidates(rawFromQuery, selectedFromStop, {
        limit: 6,
        addressRadius: 350
      });

    if (!fromCandidates.length) {
      state.navMessage = t('stopNotFound');
      renderView('navigate');
      return;
    }

    let toCandidates = state.navToSelection?.mode === 'flexible'
      ? state.navToSelection.platforms
      : await getNavigationStopCandidates(rawToQuery, selectedToStop, {
        limit: 5,
        candidateLimit: 25,
        addressRadius: 650,
        referenceStop: usingCurrentLocation ? null : fromCandidates[0]
      });

    if (usingCurrentLocation) {
      toCandidates = await expandDestinationCandidatesForNavigation(toCandidates, {
        radius: 260,
        limit: 8
      });
    }

    if (!toCandidates.length) {
      state.navMessage = t('stopNotFound');
      renderView('navigate');
      return;
    }

    const selectedDestinationDisplayStop = getSelectedDestinationDisplayStop(
      selectedToStop,
      state.navToSelection,
      toCandidates[0]
    );

    state.navFromStop = usingCurrentLocation ? null : fromCandidates[0];
    state.navToStop = selectedDestinationDisplayStop || toCandidates[0];
    state.navMessage = t('findingRoutes');
    renderView('navigate');

    const dedupeRoutes = (routes, collapseNearbyOrigins) => {
      const routeKeySet = new Set();
      return routes
        .sort(compareNavigationRoutes)
        .filter((route) => {
          const key = navigationRouteDedupeKey(route, collapseNearbyOrigins);
          if (routeKeySet.has(key)) {
            return false;
          }
          routeKeySet.add(key);
          return true;
        });
    };

    let dedupedRoutes = [];

    if (usingCurrentLocation) {
      const journeyResult = await findJourneyRoutesFromCurrentLocation(toCandidates[0]);
      fromCandidates = journeyResult.fromCandidates.length ? journeyResult.fromCandidates : fromCandidates;
      toCandidates = journeyResult.toCandidates.length ? journeyResult.toCandidates : toCandidates;
      dedupedRoutes = dedupeRoutes(journeyResult.routes, true);
    } else {
      dedupedRoutes = dedupeRoutes(
        await findRoutesForStopPairs(fromCandidates, toCandidates, { strictStops: false }),
        true
      );
    }

    dedupedRoutes = applySelectedDestinationDisplay(dedupedRoutes, selectedDestinationDisplayStop);

    state.navRoutes = groupNavigationRoutes(dedupedRoutes, usingCurrentLocation)
      .map((route) => applySelectedDestinationDisplay([route], selectedDestinationDisplayStop)[0])
      .map((route) => {
        if (route.type === 'transfer' && Array.isArray(route.transferOptions)) {
          route.transferOptions.sort((left, right) => compareLineLabels(left.line2, right.line2));
        }
        if (route.type === 'multi_transfer' && Array.isArray(route.firstLineOptions)) {
          route.firstLineOptions.sort((left, right) => compareLineLabels(left.line1, right.line1));
        }
        return route;
      })
      .sort(compareNavigationRoutes)
      .slice(0, 6);
    if (!state.navRoutes.length && state.navFromSelection?.mode === 'exact') {
      state.navFallbackSuggestion = await findExactPlatformFallback(state.navFromSelection.stop, toCandidates);
    }
    const bestRoute = state.navRoutes[0];
    if (bestRoute && usingCurrentLocation) {
      state.navFromStop = null;
      state.navToStop = selectedDestinationDisplayStop
        ? await getStopDetails(selectedDestinationDisplayStop)
        : await getStopDetails({
          ...normalizeStop({
            station_id: bestRoute.to_station_id,
            stop_id: bestRoute.dest_stop_id || bestRoute.to_station_id,
            name: bestRoute.to_station_name,
            stop_lat: bestRoute.to_stop_lat,
            stop_lon: bestRoute.to_stop_lon
          })
        });
    } else if (bestRoute) {
      const matchedFromStop = fromCandidates.find((stop) => stop.stationId === bestRoute.from_station_id);
      const matchedToStop = toCandidates.find((stop) => stop.stationId === bestRoute.to_station_id);
      state.navFromStop = await getStopDetails({
        ...normalizeStop({
          station_id: bestRoute.from_station_id,
          stop_id: bestRoute.origin_stop_id || bestRoute.from_station_id,
          name: bestRoute.from_station_name,
          stop_lat: bestRoute.from_stop_lat,
          stop_lon: bestRoute.from_stop_lon
        }),
        lines: matchedFromStop?.lines || []
      });
      state.navToStop = selectedDestinationDisplayStop
        ? await getStopDetails(selectedDestinationDisplayStop)
        : await getStopDetails({
          ...normalizeStop({
            station_id: bestRoute.to_station_id,
            stop_id: bestRoute.dest_stop_id || bestRoute.to_station_id,
            name: bestRoute.to_station_name,
            stop_lat: bestRoute.to_stop_lat,
            stop_lon: bestRoute.to_stop_lon
          }),
          lines: matchedToStop?.lines || [],
          sharedLines: matchedToStop?.sharedLines || []
        });
    }
    state.navMessage = formatNavigationResultsCount(state.navRoutes.length);

    const departureRequests = state.navRoutes.flatMap((route) => {
      const originStationId = route.origin_station_id || route.from_station_id;
      const requests = getRouteFirstLines(route).map((line) => ({
        stationId: originStationId,
        line
      }));

      if (route.type === 'multi_transfer') {
        requests.push({
          stationId: route.transfer1_to_station_id || formatPublicStopId(route.transfer1_to_stop_id || ''),
          line: route.line2
        });
        requests.push({
          stationId: route.transfer2_to_station_id || formatPublicStopId(route.transfer2_to_stop_id || ''),
          line: route.line3
        });
      } else if (route.type === 'transfer') {
        getRouteSecondLines(route).forEach((line) => requests.push({
          stationId: getRouteTransferStationId(route),
          line
        }));
      }

      return requests;
    }).filter((request) => request.stationId && request.line);

    if (departureRequests.length) {
      try {
        const uniqueDepartureChecks = Array.from(new Map(departureRequests.map((request) => [
          departureLookupKey(request.stationId, request.line),
          request
        ])).values());

        const departureChecks = await Promise.all(uniqueDepartureChecks.map(async (check) => {
          try {
            const departures = await apiRequest(`/predict/stop?station_id=${encodeURIComponent(check.stationId)}&lines=${encodeURIComponent(check.line)}`);
            return (departures.predicted_arrivals || [])
              .filter((arrival) => !arrival.error && !arrival.empty && Number(arrival.mins_remaining) <= 60)
              .map((arrival) => ({
                ...arrival,
                stationId: check.stationId,
                line: check.line
              }));
          } catch (error) {
            return [];
          }
        }));
        state.navDepartures = departureChecks.flat();
      } catch (error) {
        state.navDepartures = [];
      }
    }

    state.navMessage = formatNavigationResultsCount(getVisibleNavigationRoutes().length);
  } catch (error) {
  state.navRoutes = [];
  state.navDepartures = [];
  state.navExpandedRouteKeys = new Set();
    state.navMessage = error.message;
  }

  renderView('navigate');
  const firstVisibleRoute = getVisibleNavigationRoutes()[0];
  if (firstVisibleRoute) {
    await renderNavigationRouteOnMap(firstVisibleRoute);
  }
}

async function enrichStopCoordinates(stop) {
  if (!stop || hasCoordinates(stop)) {
    return stop;
  }

  try {
    const data = await apiRequest(`/stops?station_id=${encodeURIComponent(stop.stationId)}`);
    const match = (data.stops || [])[0];
    if (!match) {
      return stop;
    }

    return {
      ...stop,
      rawStopId: String(match.stop_id || stop.rawStopId || ''),
      lat: Number(match.stop_lat),
      lon: Number(match.stop_lon)
    };
  } catch (error) {
    return stop;
  }
}

async function loadStopArrivals() {
  if (!state.currentStop) {
    return;
  }

  const requestedStopId = state.currentStop.stationId || formatPublicStopId(state.currentStop.rawStopId);
  if (!requestedStopId) {
    state.arrivals = [];
    state.stopMessage = t('stopMissing');
    renderView('stop');
    return;
  }

  state.stopMessage = t('loadingDepartures');
  state.arrivals = [];
  renderView('stop');

  try {
    const lineParam = state.selectedDepartureLines.length
      ? `&lines=${encodeURIComponent(state.selectedDepartureLines.join(','))}`
      : '';
    const data = await apiRequest(`/predict/stop?station_id=${encodeURIComponent(requestedStopId)}${lineParam}`);
    state.arrivals = data.predicted_arrivals || [];
    if (state.selectedDepartureLines.length) {
      addRecentStop({
        ...state.currentStop,
        presetLine: state.selectedDepartureLines.join(', ')
      });
    }
    if (!state.arrivals.length) {
      state.stopMessage = t('noPredictions');
    }
  } catch (error) {
    state.arrivals = [];
    state.stopMessage = error.message;
  }

  renderView('stop');
}

async function openStop(stop) {
  state.currentStop = normalizeStop(stop);
  recordFavoriteUse(state.currentStop);
  state.activeSheet = '';
  state.selectedDepartureLines = parseLineList(state.currentStop.presetLine || '');
  state.routeDirections = [];
  state.routeLine = '';
  state.routeMessage = t('routeInitial');
  if (state.currentStop.presetLine) {
    parseLineList(state.currentStop.presetLine).forEach(addRecentLine);
  }
  renderView('stop');

  state.currentStop = await getStopDetails(state.currentStop);
  state.currentStop = await enrichStopCoordinates(state.currentStop);
  await loadStopArrivals();
}

async function loadLineRoute(line, options = {}) {
  const { standalone = false, returnResult = false, silent = false } = options;
  if (!standalone && !state.currentStop) {
    return false;
  }

  state.routeLine = line;
  state.routeDirections = [];
  state.routeMessage = t('loadingRoute');
  if (!standalone && !silent) {
    renderView('stop');
  }

  try {
    const routeParams = new URLSearchParams({ line });
    if (!standalone && state.currentStop?.stationId) {
      routeParams.set('station_id', state.currentStop.stationId);
    }
    const data = await apiRequest(`/route?${routeParams.toString()}`);
    state.routeDirections = data.directions || [];
    state.routeMessage = state.routeDirections.length
      ? `${t('showingRoute')} ${line}.`
      : t('noRoute');
    if (state.routeDirections.length) {
      addRecentLine(line);
    }
  } catch (error) {
    state.routeDirections = [];
    state.routeMessage = error.message === 'Line does not exist' ? t('lineNotFound') : error.message;
  }

  if (returnResult) {
    return Boolean(state.routeDirections.length);
  }

  if (standalone) {
    if (!silent) {
      renderView('home');
      renderRouteOnMap(state.routeDirections);
    }
    return;
  }

  if (!silent) {
    renderView('stop');
    renderRouteOnMap(state.routeDirections);
  }
  return Boolean(state.routeDirections.length);
}

async function saveCurrentStopToFavorites(name, line = '') {
  if (!state.currentStop) {
    return;
  }

  const favoriteName = name.trim();
  const presetLine = normalizeLineList(line);
  if (!favoriteName) {
    return;
  }

  try {
    await apiRequest('/favorites', {
      method: 'POST',
      auth: true,
      body: {
        name: favoriteName,
        station_id: state.currentStop.stationId,
        line: presetLine || null
      }
    });
    if (presetLine) {
      parseLineList(presetLine).forEach(addRecentLine);
    }
    await loadFavorites();
  } catch (error) {
    state.stopMessage = error.message === 'Line does not exist'
      ? t('lineNotFound')
      : error.message;
    renderView('stop');
  }
}

async function saveNavigationRouteToFavorites(route) {
  if (!route) {
    return;
  }

  if (!isAuthenticated()) {
    openAuth('login', 'navigate');
    return;
  }

  const payload = getRouteFavoritePayload(route);
  if (!payload.station_id || !payload.name) {
    state.navMessage = t('stopMissing');
    renderView('navigate');
    return;
  }

  try {
    await apiRequest('/favorites', {
      method: 'POST',
      auth: true,
      body: payload
    });
    parseLineList(payload.line || '').forEach(addRecentLine);
    state.navMessage = t('routeSaved');
    await loadFavorites();
    renderView('navigate');
  } catch (error) {
    state.navMessage = error.message === 'Line does not exist'
      ? t('lineNotFound')
      : error.message;
    renderView('navigate');
  }
}

async function deleteFavorite(name) {
  try {
    await apiRequest(`/favorites/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      auth: true
    });
    await loadFavorites();
  } catch (error) {
    if (state.currentView === 'favorites') {
      state.favoritesMessage = error.message;
      renderView('favorites');
    }
  }
}

async function updateFavorite(oldName, values) {
  state.favoriteEditMessage = '';

  try {
    await apiRequest(`/favorites/${encodeURIComponent(oldName)}`, {
      method: 'PUT',
      auth: true,
      body: values
    });
    state.editingFavoriteName = '';
    await loadFavorites();
  } catch (error) {
    state.favoriteEditMessage = error.message === 'Station does not exist'
      ? t('stationNotFound')
      : error.message === 'Line does not exist'
        ? t('lineNotFound')
        : error.message;
    renderView('favorites');
  }
}

async function handleAuthSubmit(event, mode) {
  event.preventDefault();
  const username = document.getElementById('auth-username').value.trim();
  const password = document.getElementById('auth-password').value;
  const errorBanner = document.getElementById('auth-error');

  errorBanner.hidden = true;
  errorBanner.classList.remove('info-banner');
  errorBanner.textContent = '';

  try {
    const data = await apiRequest(`/${mode === 'login' ? 'login' : 'register'}`, {
      method: 'POST',
      body: { username, password }
    });

    if (mode === 'login') {
      setToken(data.token);
      await loadFavorites();
      renderView(state.authReturnView === 'stop' ? 'stop' : state.authReturnView);
      return;
    }

    state.authMode = 'login';
    renderView('auth');
  } catch (error) {
    errorBanner.hidden = false;
    errorBanner.textContent = error.message;
  }
}

async function handlePasswordResetRequest(event) {
  event.preventDefault();
  const username = document.getElementById('auth-username').value.trim();
  const errorBanner = document.getElementById('auth-error');

  errorBanner.hidden = true;
  errorBanner.classList.remove('info-banner');
  errorBanner.textContent = '';

  try {
    const data = await apiRequest('/password-reset/request', {
      method: 'POST',
      body: { username }
    });
    state.authMode = 'reset-confirm';
    renderView('auth');
    const banner = document.getElementById('auth-error');
    banner.hidden = false;
    banner.classList.add('info-banner');
    banner.textContent = data.reset_token
      ? `${t('resetToken')}: ${data.reset_token}`
      : t('resetSent');
  } catch (error) {
    errorBanner.hidden = false;
    errorBanner.textContent = error.message;
  }
}

async function handlePasswordResetConfirm(event) {
  event.preventDefault();
  const token = document.getElementById('auth-reset-token').value.trim();
  const password = document.getElementById('auth-password').value;
  const errorBanner = document.getElementById('auth-error');

  errorBanner.hidden = true;
  errorBanner.classList.remove('info-banner');
  errorBanner.textContent = '';

  try {
    await apiRequest('/password-reset/confirm', {
      method: 'POST',
      body: { token, password }
    });
    state.authMode = 'login';
    renderView('auth');
    const banner = document.getElementById('auth-error');
    banner.hidden = false;
    banner.classList.add('info-banner');
    banner.textContent = t('passwordUpdated');
  } catch (error) {
    errorBanner.hidden = false;
    errorBanner.textContent = error.message;
  }
}

function getStopFromElement(element) {
  return normalizeStop({
    station_id: element.dataset.stopId,
    stop_id: element.dataset.stopRawId,
    name: element.dataset.stopName,
    stop_lat: element.dataset.stopLat,
    stop_lon: element.dataset.stopLon,
    presetLine: element.dataset.stopLine
  });
}

function wireNavigation() {
  const logo = document.querySelector('.app-logo');
  if (logo) {
    logo.addEventListener('click', (event) => {
      event.preventDefault();
      state.activeSheet = '';
      renderView('home');
    });
  }

  bottomNav.addEventListener('click', async (event) => {
    const button = event.target.closest('.nav-btn');
    if (!button) {
      return;
    }

    const target = button.dataset.target;
    state.activeSheet = '';
    if (target === 'favorites') {
      await loadFavorites();
    }
    renderView(target);
  });

  contentArea.addEventListener('click', async (event) => {
    const actionTarget = event.target.closest('[data-action]');
    if (!actionTarget) {
      return;
    }

    const action = actionTarget.dataset.action;

    if (action === 'close-sheet' && event.target.closest('[data-sheet-panel]') && !event.target.closest('button')) {
      return;
    }

    if (action === 'close-dialog' && event.target.closest('[data-dialog-panel]')) {
      return;
    }

    if (action === 'open-auth') {
      openAuth(actionTarget.dataset.mode || 'login', actionTarget.dataset.return || state.currentView);
      return;
    }

    if (action === 'open-sheet') {
      state.activeSheet = actionTarget.dataset.sheet || '';
      renderView(state.currentView);
      return;
    }

    if (action === 'close-sheet') {
      state.activeSheet = '';
      renderView(state.currentView);
      return;
    }

    if (action === 'close-dialog') {
      closeTopOverlay();
      return;
    }

    if (action === 'close-favorite-choice') {
      state.favoriteChoiceStop = null;
      renderView(state.currentView);
      return;
    }

    if (action === 'switch-auth-mode') {
      state.authMode = actionTarget.dataset.mode || 'login';
      renderView('auth');
      return;
    }

    if (action === 'return-from-auth') {
      renderView(state.authReturnView || 'profile');
      return;
    }

    if (action === 'logout') {
      logout();
      return;
    }

    if (action === 'set-language') {
      setLanguage(actionTarget.dataset.language);
      resetLocalizedMessages();
      renderView(state.currentView);
      return;
    }

    if (action === 'locate-nearby') {
      await findNearbyStops({ useBrowserLocation: true });
      return;
    }

    if (action === 'use-nav-location') {
      await useCurrentLocationAsNavigationStart();
      return;
    }

    if (action === 'start-map-pick') {
      state.mapPickMode = true;
      state.mapPickCandidate = null;
      state.mapPickOptions = [];
      state.mapPickMessage = '';
      state.favoriteChoiceStop = null;
      state.activeSheet = '';
      renderView(state.currentView);
      if (map) {
        setTimeout(() => map.invalidateSize(), 0);
      }
      return;
    }

    if (action === 'choose-different-stop') {
      state.mapPickCandidate = null;
      state.mapPickOptions = [];
      state.mapPickMessage = '';
      state.favoriteChoiceStop = null;
      state.mapPickMode = true;
      renderView(state.currentView);
      return;
    }

    if (action === 'select-map-pick-stop') {
      const stop = state.mapPickOptions[Number(actionTarget.dataset.stopIndex)];
      if (stop) {
        await showStopChoice(stop);
      }
      return;
    }

    if (action === 'confirm-picked-stop') {
      const stop = state.mapPickCandidate;
      state.mapPickCandidate = null;
      state.mapPickOptions = [];
      state.mapPickMessage = '';
      state.mapPickMode = false;
      if (stop) {
        await openStop(stop);
      } else {
        renderView(state.currentView);
      }
      return;
    }

    if (action === 'refresh-nearby') {
      await findNearbyStops();
      return;
    }

    if (action === 'toggle-nearby') {
      state.nearbyExpanded = !state.nearbyExpanded;
      renderView('home');
      return;
    }

    if (action === 'toggle-search-results') {
      state.searchResultsExpanded = !state.searchResultsExpanded;
      renderView('search');
      return;
    }

    if (action === 'clear-recent-lines') {
      state.recentLines = [];
      localStorage.removeItem('recentLines');
      renderView(state.currentView);
      return;
    }

    if (action === 'toggle-recent-lines') {
      state.recentLinesExpanded = !state.recentLinesExpanded;
      renderView('home');
      return;
    }

    if (action === 'open-stop') {
      state.favoriteChoiceStop = null;
      await openStop(getStopFromElement(actionTarget));
      return;
    }

    if (action === 'open-favorite-preset') {
      state.favoriteChoiceStop = null;
      await openStop(getStopFromElement(actionTarget));
      return;
    }

    if (action === 'open-favorite-custom') {
      state.favoriteChoiceStop = null;
      const stop = getStopFromElement(actionTarget);
      await openStop({
        ...stop,
        presetLine: ''
      });
      return;
    }

    if (action === 'open-favorite-choice-preset') {
      state.favoriteChoiceStop = null;
      await openStop(getStopFromElement(actionTarget));
      return;
    }

    if (action === 'edit-favorite') {
      state.editingFavoriteName = actionTarget.dataset.favoriteName || '';
      state.favoriteEditMessage = '';
      renderView('favorites');
      return;
    }

    if (action === 'cancel-edit-favorite') {
      state.editingFavoriteName = '';
      state.favoriteEditMessage = '';
      renderView('favorites');
      return;
    }

    if (action === 'go-back') {
      renderView(state.previousView || 'home');
      return;
    }
    if (action === 'toggle-nav-suggestion-group') {
      const key = `${actionTarget.dataset.suggestionType}:${actionTarget.dataset.groupKey}`;
      if (state.navExpandedSuggestionKeys.has(key)) {
        state.navExpandedSuggestionKeys.delete(key);
      } else {
        state.navExpandedSuggestionKeys.add(key);
      }
      renderView('navigate');
      return;
    }
    if (action === 'select-nav-from-group') {
      const group = findSuggestionByGroup('from', actionTarget.dataset.groupKey || '');
      if (group) {
        await selectNavigationStartGroup(group);
      }
      return;
    }
    if (action === 'select-nav-to-group') {
      const group = findSuggestionByGroup('to', actionTarget.dataset.groupKey || '');
      if (group) {
        await selectNavigationDestinationGroup(group);
      }
      return;
    }
    if (action === 'use-nav-fallback') {
      await selectNavigationStart(normalizeStop({
        station_id: actionTarget.dataset.stopId,
        stop_id: actionTarget.dataset.stopRawId,
        name: actionTarget.dataset.stopName,
        stop_lat: actionTarget.dataset.stopLat,
        stop_lon: actionTarget.dataset.stopLon
      }));
      state.navFallbackSuggestion = null;
      const toInput = document.getElementById('nav-to-input');
      if (toInput?.value.trim()) {
        await findNavigationRoutes(formatStationLabel(state.navFromStop), toInput.value);
      }
      return;
    }
    if (action === 'select-nav-from') {
      await selectNavigationStart(normalizeStop({
        station_id: actionTarget.dataset.stopId,
        stop_id: actionTarget.dataset.stopRawId,
        name: actionTarget.dataset.stopName,
        stop_lat: actionTarget.dataset.stopLat,
        stop_lon: actionTarget.dataset.stopLon
      }));
      return;
    }
    if (action === 'select-nav-to') {
      const selectedStop = normalizeStop({
        station_id: actionTarget.dataset.stopId,
        stop_id: actionTarget.dataset.stopRawId,
        name: actionTarget.dataset.stopName,
        stop_lat: actionTarget.dataset.stopLat,
        stop_lon: actionTarget.dataset.stopLon
      });
      selectedStop.sharedLines = [];
      await selectNavigationDestination(selectedStop);
      return;
    }

    if (action === 'reload-stop') {
      await loadStopArrivals();
      return;
    }

    if (action === 'toggle-departure-line') {
      const line = actionTarget.dataset.line || '';
      if (state.selectedDepartureLines.includes(line)) {
        state.selectedDepartureLines = state.selectedDepartureLines.filter((value) => value !== line);
      } else {
        state.selectedDepartureLines = [...state.selectedDepartureLines, line].sort(compareLineLabels);
      }
      renderView('stop');
      return;
    }

    if (action === 'select-all-departure-lines') {
      state.selectedDepartureLines = getAvailableArrivalLines();
      renderView('stop');
      return;
    }

    if (action === 'clear-departure-lines') {
      state.selectedDepartureLines = [];
      renderView('stop');
      return;
    }

    if (action === 'show-route') {
      await loadLineRoute(actionTarget.dataset.line || '');
      return;
    }

    if (action === 'show-nav-route') {
      const route = state.navRoutes[Number(actionTarget.dataset.routeIndex)];
      await renderNavigationRouteOnMap(route);
      return;
    }

    if (action === 'save-nav-route') {
      const route = state.navRoutes[Number(actionTarget.dataset.routeIndex)];
      await saveNavigationRouteToFavorites(route);
      return;
    }

    if (action === 'toggle-nav-route-details') {
      const routeKey = actionTarget.dataset.routeKey || '';
      if (state.navExpandedRouteKeys.has(routeKey)) {
        state.navExpandedRouteKeys.delete(routeKey);
      } else {
        state.navExpandedRouteKeys.add(routeKey);
      }
      renderView('navigate');
      return;
    }

    if (action === 'open-recent-item') {
      const recentItem = state.recentLines[Number(actionTarget.dataset.recentIndex)];
      if (!recentItem) {
        return;
      }

      if (recentItem.type === 'stop') {
        await openStop(recentItem);
        return;
      }

      await loadLineRoute(recentItem.line || '', { standalone: true });
      return;
    }

    if (action === 'delete-favorite') {
      await deleteFavorite(actionTarget.dataset.favoriteName || '');
    }
  });

  contentArea.addEventListener('submit', async (event) => {
    if (event.target.id === 'home-search-form') {
      event.preventDefault();
      const query = document.getElementById('home-search-input').value;
      await searchStops(query);
      return;
    }

    if (event.target.id === 'search-form') {
      event.preventDefault();
      const query = document.getElementById('search-input').value;
      await searchStops(query);
      return;
    }

    if (event.target.id === 'navigation-form') {
      event.preventDefault();
      const from = document.getElementById('nav-from-input').value;
      const to = document.getElementById('nav-to-input').value;
      await findNavigationRoutes(from, to);
      return;
    }

    if (event.target.id === 'login-form') {
      await handleAuthSubmit(event, 'login');
      return;
    }

    if (event.target.id === 'register-form') {
      await handleAuthSubmit(event, 'register');
      return;
    }

    if (event.target.id === 'password-reset-request-form') {
      await handlePasswordResetRequest(event);
      return;
    }

    if (event.target.id === 'password-reset-confirm-form') {
      await handlePasswordResetConfirm(event);
      return;
    }

    if (event.target.id === 'favorite-form') {
      event.preventDefault();
      const label = document.getElementById('favorite-name-input').value;
      const line = document.getElementById('favorite-line-input').value;
      await saveCurrentStopToFavorites(label, line);
      return;
    }

    if (event.target.classList.contains('favorite-edit-form')) {
      event.preventDefault();
      const formData = new FormData(event.target);
      await updateFavorite(event.target.dataset.favoriteName || '', {
        name: String(formData.get('name') || '').trim(),
        station_id: String(formData.get('station_id') || '').trim(),
        line: normalizeLineList(formData.get('line') || '') || null
      });
    }
  });

  contentArea.addEventListener('input', async (event) => {
    if (event.target.id === 'nav-from-input') {
      clearTimeout(navFromInputTimer);
      const { value } = event.target;
      const requestId = ++navFromRequestId;
      state.navFromQuery = value;
      state.navFromStop = null;
      state.navFromSelection = null;
      state.navFromLocation = null;
      state.navFromLocationCandidates = [];
      state.navFromLocationAllCandidates = [];
      state.navFromLoading = Boolean(value.trim());
      state.navFromSuggestions = value.trim() ? state.navFromSuggestions : [];
      state.navRoutes = [];
      state.navDepartures = [];
      state.navExpandedRouteKeys = new Set();
      state.navMessage = value.trim() ? t('chooseBothStops') : t('chooseStartFirst');
      navFromInputTimer = setTimeout(() => {
        loadNavigationStartSuggestions(value, requestId);
      }, 250);
      return;
    }
    if (event.target.id === 'nav-to-input') {
      clearTimeout(navToInputTimer);
      const { value } = event.target;
      const requestId = ++navToRequestId;
      state.navToQuery = value;
      state.navToStop = null;
      state.navToSelection = null;
      state.navToLoading = Boolean(value.trim());
      state.navToSuggestions = value.trim() ? state.navToSuggestions : [];
      state.navRoutes = [];
      state.navDepartures = [];
      state.navExpandedRouteKeys = new Set();
      state.navMessage = t('chooseBothStops');
      navToInputTimer = setTimeout(() => {
        loadNavigationDestinationSuggestions(value, requestId);
      }, 250);
    }
  });

  contentArea.addEventListener('keydown', (event) => {
    const dialog = event.target.closest('[role="dialog"]');
    if (!dialog) {
      return;
    }

    if (event.key === 'Escape') {
      event.preventDefault();
      closeTopOverlay();
      return;
    }

    if (event.key !== 'Tab') {
      return;
    }

    const focusables = getFocusableElements(dialog);
    if (!focusables.length) {
      event.preventDefault();
      dialog.focus();
      return;
    }

    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  contentArea = document.getElementById('content-area');
  mapContainer = document.getElementById('map');
  bottomNav = document.getElementById('bottom-nav');
  appShell = document.getElementById('app');

  setLanguage(state.language);
  state.recentLines = loadRecentLines();
  state.favoriteUsage = loadFavoriteUsage();
  resetLocalizedMessages();
  ensureMap();
  wireNavigation();
  renderView('home');

  await findNearbyStops({ useBrowserLocation: true });
  if (isAuthenticated()) {
    await loadFavorites();
  }
});
