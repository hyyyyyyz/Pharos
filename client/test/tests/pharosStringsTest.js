"use strict";

/**
 * The strings that are built rather than written.
 *
 * Most Pharos ids are literals, and a missing one shows up the moment the code
 * that names it runs. These do not: they are assembled at runtime from a backend
 * enum -- `'pharos-projects-stage-' + project.stage` and its neighbours -- so the
 * id that fails is the one for a value nobody has seen yet, on the day the
 * backend starts sending it.
 *
 * That failure is not cosmetic. `Zotero.getString()` reads pharos.ftl through
 * formatValueSync, falls through to the .properties bundle when the id has no
 * value there, and THROWS in en-US (intl.js). In zh-CN it returns the bare id.
 * So one unhandled enum value either takes a whole window's render down or puts
 * `pharos-projects-stage-synthesis` on screen where a stage name belongs.
 *
 * Both halves are checked below: that every value the client itself knows about
 * resolves today, and that the call sites which cannot know -- the ones handed a
 * value straight off the wire -- are wrapped so an unknown one degrades instead
 * of throwing.
 */
describe("Pharos runtime-built strings", function () {
	/**
	 * Resolve an id the way the product does, and report a miss as a miss.
	 *
	 * Deliberately not a bare `Zotero.getString`: this has to distinguish three
	 * outcomes, and getString collapses two of them by locale. Returns null when
	 * the id does not resolve, whichever way this build fails.
	 */
	function resolve(id) {
		let value;
		try {
			value = Zotero.getString(id);
		}
		catch (e) {
			return null; // en-US: throws
		}
		// zh-CN and any other non-English locale: returns the id itself.
		return value === id ? null : value;
	}

	describe("research project enums", function () {
		it("should name every workflow stage", function () {
			let missing = Zotero.Pharos.Projects.STAGES
				.filter(stage => !resolve('pharos-projects-stage-' + stage));
			assert.deepEqual(missing, [],
				"a stage with no string renders as its own id, or throws in en-US");
		});

		it("should name every artifact type", function () {
			// The `_` to `-` mangling is the id spelling, not the enum's: the wire
			// value is experiment_plan and the Fluent id is -experiment-plan. It
			// was inlined at four call sites once and wrong at one of them.
			let missing = Zotero.Pharos.Projects.ARTIFACT_TYPES
				.filter(type => !resolve('pharos-projects-type-' + type.replace(/_/g, '-')));
			assert.deepEqual(missing, []);
		});

		it("should name every artifact status", function () {
			let missing = Zotero.Pharos.Projects.ARTIFACT_STATUSES
				.filter(status => !resolve('pharos-projects-status-' + status));
			assert.deepEqual(missing, []);
		});

		// A value from a NEWER backend -- one this client has never heard of --
		// must not be able to take a window down. Enumerating the values we know
		// about cannot show that; only an unknown one can.
		//
		// What the bare helper does with one is LOCALE-DEPENDENT: it throws in
		// en-US and returns the id in every other locale. So asserting either
		// behaviour here would pin a test to whichever locale the suite happens
		// to run in, and would pass on this machine while the product broke on
		// somebody else's. What is testable, and is the thing that actually
		// matters, is that the callers survive it either way.
		it("should export an unknown stage, type and status without throwing", async function () {
			let note = await Zotero.Pharos.Projects.saveArtifactAsNote(
				{
					id: 'a1',
					title: 'A record',
					body: 'Body.',
					stage: 'no-such-stage',
					type: 'no_such_type',
					status: 'no-such-status',
				},
				{ name: 'A project', automation_notice: 'Nothing ran this.' }
			);
			assert.ok(note, "an enum this build predates must not lose the export");
			// And the unknown value is legible rather than blank -- a placeholder
			// a reader can search for beats a gap they cannot explain.
			assert.include(note.getNote(), 'no-such-stage');
		});
	});

	// The other half of this invariant -- that both locale files define the SAME
	// ids -- is deliberately NOT here. Fluent resolves pharos.ftl through the
	// L10nRegistry rather than through chrome://, so this harness has no path to
	// the sibling locale, and every attempt to construct one either read the
	// active locale twice (comparing a file with itself, which passes
	// unconditionally) or hardcoded a directory layout that differs between the
	// source tree, the staged app and a packaged build.
	//
	// It lives in test/check-locale-parity.js instead, where reading two files is
	// the trivial operation it should be. Run it from client/:
	//
	//     node test/check-locale-parity.js
});
