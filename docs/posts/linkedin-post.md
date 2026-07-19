# LinkedIn post

Most AI planning tools take a prompt and hand back a plausible plan. It sounds right because it is generic — it has never seen your repository.

PFactory, the Plan stage of our autonomous Factory pipeline, starts the other way around. Before it writes any plan, it clones the target repo read-only and builds a RepoMap of it: languages, frameworks, package managers, infrastructure-as-code, and the exact commit it read. Then it classifies the change, grounds the plan in the repository's real delivery history (DORA), enriches it from house standards and the live Backstage catalog, runs an injection scan, and scores its own feasibility and architecture review lenses. The output is a signed Task Contract about that specific codebase — not a guess extrapolated from a sentence.

The plan then drives the rest of the factory: build in a throwaway Kubernetes Job, autonomous test generation and execution, and a verdict threaded back onto the pull request the factory opened.

The part I would point a skeptic to is a refusal. This cycle, a helper built and looked fine but failed one test verdict on a unicode edge case. The verification gate capped it at VAL-0 and auto-filed a handback to fix it. It would not certify a build with a failing test. That is the design, not a bug.

A live walkthrough of all four portals is available on request.

#SoftwareEngineering #AIAgents #DevOps #PlatformEngineering #CodeReview #ContinuousDelivery #Backstage #Kubernetes
