/*
 * MongoDB 8.x / mongosh. No documents are inserted, deleted, or migrated.
 * Usage: mongosh "mongodb://127.0.0.1:27017/survey_system" --file database/init-mongodb.js
 * Existing data must be audited before use: collMod does not scan old documents.
 * Index/validator errors stop this script; earlier DDL changes are not rolled back.
 */
(() => {
  const integer = { bsonType: ["int", "long"] };
  const numeric = { bsonType: ["int", "long", "double", "decimal"] };
  const id = { bsonType: "string", pattern: "^[A-Za-z][A-Za-z0-9_-]{0,63}$" };
  const dateOrNull = { bsonType: ["date", "null"] };
  const text = (max, min = 0) => ({ bsonType: "string", minLength: min, maxLength: max });
  const object = (properties, required = Object.keys(properties)) => ({
    bsonType: "object", additionalProperties: false,
    ...(required.length ? { required } : {}), properties
  });
  const array = (items, minItems, maxItems, uniqueItems = false) => ({
    bsonType: "array", items, minItems, maxItems, ...(uniqueItems ? { uniqueItems: true } : {})
  });
  const nonNegativeInt = (max) => ({ ...integer, minimum: 0, maximum: max });
  const target = {
    oneOf: [
      object({ kind: { enum: ["question"] }, questionId: id }),
      object({ kind: { enum: ["end"] } })
    ]
  };
  const singleCondition = object({ operator: { enum: ["eq"] }, value: id });
  const multipleCondition = object({
    operator: { enum: ["containsAny", "containsAll", "setEquals"] },
    values: array(id, 1, 50, true)
  });
  const numberCondition = {
    oneOf: [
      object({ operator: { enum: ["gt", "gte", "lt", "lte", "eq"] }, value: numeric }),
      object({ operator: { enum: ["between"] }, min: numeric, max: numeric })
    ]
  };
  const rules = (condition) => array(object({ id, condition, target }), 0, 20);
  const options = array(object({ id, label: text(500, 1) }), 2, 50);
  const baseQuestion = { id, title: text(2000, 1), description: text(2000), required: { bsonType: "bool" } };
  const question = (type, validation, extra = {}) => object({
    ...baseQuestion, type: { enum: [type] }, validation, ...extra
  });
  const questionSchema = {
    oneOf: [
      question("single", object({}), { options, jumpRules: rules(singleCondition) }),
      question("multiple", object({
        minSelections: nonNegativeInt(50), maxSelections: nonNegativeInt(50)
      }, []), { options, jumpRules: rules(multipleCondition) }),
      question("text", object({
        minLength: nonNegativeInt(10000), maxLength: nonNegativeInt(10000)
      }, []), { jumpRules: { bsonType: "array", maxItems: 0 } }),
      question("number", object({
        min: numeric, max: numeric, integerOnly: { bsonType: "bool" }
      }, ["integerOnly"]), { jumpRules: rules(numberCondition) })
    ]
  };
  const answerValue = {
    oneOf: [
      { bsonType: "null" }, text(10000, 1), array(id, 1, 50, true), numeric
    ]
  };
  const definitions = {
    users: {
      schema: object({
        _id: { bsonType: "objectId" }, username: text(32, 3), usernameKey: text(128, 3),
        passwordHash: text(1024, 1), createdAt: { bsonType: "date" }, updatedAt: { bsonType: "date" }
      }),
      indexes: [{ key: { usernameKey: 1 }, name: "uq_users_usernameKey", unique: true }]
    },
    surveys: {
      schema: object({
        _id: { bsonType: "objectId" }, ownerId: { bsonType: "objectId" },
        publicId: { bsonType: "string", pattern: "^[0-9a-f]{32}$" },
        title: text(200, 1), description: text(5000), status: { enum: ["draft", "published", "closed"] },
        settings: object({ anonymous: { bsonType: "bool" }, allowRepeat: { bsonType: "bool" } }),
        deadlineAt: dateOrNull, questions: array(questionSchema, 0, 100),
        schemaVersion: { ...integer, enum: [1] }, revision: { ...integer, minimum: 1 },
        createdAt: { bsonType: "date" }, updatedAt: { bsonType: "date" },
        publishedAt: dateOrNull, closedAt: dateOrNull, closeReason: { enum: [null, "manual", "deadline"] }
      }),
      indexes: [
        { key: { publicId: 1 }, name: "uq_surveys_publicId", unique: true },
        { key: { ownerId: 1, createdAt: -1, _id: -1 }, name: "ix_surveys_owner_created" }
      ]
    },
    responses: {
      schema: object({
        _id: { bsonType: "objectId" }, surveyId: { bsonType: "objectId" },
        respondentId: { bsonType: "objectId" },
        submissionKey: { bsonType: "string", pattern: "^[0-9a-f]{64}$" },
        answers: array(object({ questionId: id, value: answerValue }), 1, 100),
        submittedAt: { bsonType: "date" }, schemaVersion: { ...integer, enum: [1] }
      }, ["_id", "surveyId", "submissionKey", "answers", "submittedAt", "schemaVersion"]),
      indexes: [
        { key: { surveyId: 1, submittedAt: -1, _id: -1 }, name: "ix_responses_survey_submitted" },
        { key: { surveyId: 1, submissionKey: 1 }, name: "uq_responses_survey_submission", unique: true }
      ]
    },
    participations: {
      schema: object({
        _id: { bsonType: "objectId" }, surveyId: { bsonType: "objectId" }, userId: { bsonType: "objectId" }
      }),
      indexes: [{ key: { surveyId: 1, userId: 1 }, name: "uq_participations_survey_user", unique: true }]
    }
  };
  // Expose definitions to a read-only Node export/QA harness; do not run DB operations there.
  if (typeof module !== "undefined" && module.exports && typeof db === "undefined") {
    module.exports = definitions;
    return;
  }
  for (const [name, definition] of Object.entries(definitions)) {
    const validator = { $jsonSchema: definition.schema };
    if (db.getCollectionInfos({ name }).length === 0) {
      db.createCollection(name, { validator, validationLevel: "strict", validationAction: "error" });
    } else {
      const result = db.runCommand({ collMod: name, validator, validationLevel: "strict", validationAction: "error" });
      if (!result.ok) throw new Error("collMod failed for " + name + ": " + JSON.stringify(result));
    }
    for (const { key, ...options } of definition.indexes) {
      db.getCollection(name).createIndex(key, options);
    }
    print("Configured " + db.getName() + "." + name);
  }
})();
