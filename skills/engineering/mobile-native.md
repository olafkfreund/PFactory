# mobile-native

> Source: https://github.com/olafkfreund/PFactory | v0.1.0 | License: MIT | Tags: mobile, ios, android, swift, kotlin, app-store, play-store, accessibility, release

---

# Native Mobile Engineering

Use this skill when building or planning native iOS/Android work: Swift, Kotlin, store submission, runtime permissions, offline behaviour, deep links, crash reporting, minimum OS versions, VoiceOver/TalkBack, app size and battery, staged rollout, or forced upgrade. This is the build-side companion to the `mobile-app` plan type's implicit requirements: PFactory injects them as acceptance criteria; this skill describes what satisfying each actually involves.

---

The `mobile-app` plan type injects ten implicit requirements into every mobile
epic (`plan/decompose/implicit_requirements.py`, `MOBILE_IMPLICIT_REQUIREMENTS`).
They exist because briefs state features and omit the things a store reviewer,
a bad network, or a screen reader will find. Each section below is one
requirement: what "done" involves and where teams get rejected.

## 1. Store listing (`store-listing`)

A complete listing is name, description, screenshots per device class, and —
the part that fails review — accurate privacy declarations: Apple's privacy
nutrition labels and Google's Data safety form must match what the binary
actually does. Common rejection causes: privacy declarations contradicting
observed network traffic; login-gated apps without a demo account for the
reviewer; broken links to privacy policy; screenshots showing UI the build
does not contain. Read the actual policies, not summaries: Apple
https://developer.apple.com/app-store/review/guidelines/ and Play
https://support.google.com/googleplay/android-developer/answer/9859455.

## 2. Permission prompts (`permission-prompts`)

Request each runtime permission in context — when the user first uses the
feature needing it — with a rationale shown first. Requesting everything at
first launch is both a rejection risk and the highest-denial pattern. iOS
purpose strings (`NSLocationWhenInUseUsageDescription` etc.) must say
specifically why; generic strings are rejected. Android 13+ notification
permission is a runtime request. Every permission needs a denial path: the
feature degrades, explains, and offers a route to Settings — it does not
re-prompt in a loop (iOS will not even show the second prompt).

## 3. Offline and poor network (`offline`)

The app remains usable offline: cached content renders, user actions queue
and sync, and failures say what happened. Test the ugly middle, not just
airplane mode: 2G-class latency, a request that dies mid-body, a captive
portal returning 200 with an HTML login page. Writes need an outbox with
retry and conflict policy; reads need cache-then-network with a visible
staleness cue. "Requires connection" screens are acceptable only for flows
that genuinely cannot work offline, and they must not lose user input.

## 4. Deep links (`deep-links`)

Universal links (iOS) and app links (Android) open the correct in-app screen
including from a cold start — the case that breaks, because the navigation
stack does not exist yet. Route through a resolver that builds the back
stack rather than pushing onto whatever exists. Verify domain association
(`apple-app-site-association`, `assetlinks.json`) is served correctly, and
test the not-installed and logged-out cases: the link should survive login
and still land on the target.

## 5. Crash reporting (`crash-reporting`)

Wired and symbolicated: a production crash must produce a readable stack
trace attributable to a release. That means dSYM upload (iOS) and
mapping-file upload (Android, when R8/ProGuard is on) are build-pipeline
steps that fail the build when they fail — a silent symbol-upload failure is
how a team ships blind for a month. Include OS version, device class, and
release channel as dimensions; alert on new-crash-signature and on
crash-free-sessions dropping below the stated budget.

## 6. Minimum OS versions (`min-os-versions`)

Declare the minimum iOS version and Android `minSdk` explicitly, as a
decision with data (what share of the target audience each floor excludes),
not as whatever the template defaulted to. Every API newer than the floor
needs an availability check or a declared graceful degradation. CI should
run against the floor, not only the latest — the floor is where the crashes
live.

## 7. Accessibility (`accessibility`)

Core flows operable with VoiceOver (iOS) and TalkBack (Android): every
interactive element labelled, focus order logical, custom controls exposing
role and state, touch targets at platform minimums, text surviving dynamic
type. This is testable, not aspirational: drive each core flow with the
screen reader on and fix what cannot be completed. Android Studio's
Accessibility Scanner and Xcode's Accessibility Inspector catch the
mechanical part; the flow walk-through catches the rest.

## 8. Size and battery budgets (`size-battery`)

State the budgets, then enforce them in CI: download size cap checked per
build (Play reports it directly; on iOS track the App Store Connect
estimate), and a battery/background-work budget. The usual offenders are
unstripped assets, all-density image bundles instead of app-bundle/thinning,
and location or network polling in the background. A budget without a CI
check regresses silently — treat both like test coverage.

## 9. Release channel and staged rollout (`release-rollout`)

Ship through TestFlight / Play internal testing, then release by staged
rollout (Play percentage rollout; App Store phased release) with a halt
criterion stated in advance: at what crash-free rate or error signal do you
pause the rollout? A staged rollout nobody watches is a full rollout with
extra steps. Keep the previous release shippable — hotfixing through review
takes days; store review time is part of your incident-response budget.

## 10. Forced upgrade (`forced-upgrade`)

Build the mechanism before you need it: a remotely readable
minimum-supported-version that the app checks and enforces with a blocking
upgrade screen. The day a security fix or a broken API contract requires
killing old clients is the wrong day to discover old clients cannot be
killed. Test the block screen path as a real flow: it must work when
everything else in the app is broken.

## The honesty boundary: what your environment can verify

Kotlin/Android and Swift-core (SwiftPM + XCTest) logic can build and test on
Linux. SwiftUI/UIKit UI tests, the iOS simulator, `xcodebuild`, and `.ipa`
signing require macOS — where no macOS runner exists, those lanes CANNOT run.
Check the task contract's environment manifest for what your runner actually
provides rather than assuming, and report an unrunnable lane as not run,
with the reason — never as passed and never quietly omitted (RFC-0006:
verification levels are never overclaimed). A green checkmark on a lane that
could not execute is the defect the factory's whole verification stack
exists to prevent.
