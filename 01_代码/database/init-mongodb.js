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
  const positiveInt = { ...integer, minimum: 1, maximum: 9007199254740991 };
  const guardRevision = { ...integer, minimum: 0, maximum: 9007199254740991 };
  const objectId = { bsonType: "objectId" };
  const sha256 = { bsonType: "string", pattern: "^[0-9a-f]{64}$" };
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
  const question = (type, validation, extra = {}, reference = {}) => object({
    ...baseQuestion, ...reference, type: { enum: [type] }, validation, ...extra
  });
  const questionSchemaFor = (reference = {}) => ({
    oneOf: [
      question("single", object({}), { options, jumpRules: rules(singleCondition) }, reference),
      question("multiple", object({
        minSelections: nonNegativeInt(50), maxSelections: nonNegativeInt(50)
      }, []), { options, jumpRules: rules(multipleCondition) }, reference),
      question("text", object({
        minLength: nonNegativeInt(10000), maxLength: nonNegativeInt(10000)
      }, []), { jumpRules: { bsonType: "array", maxItems: 0 } }, reference),
      question("number", object({
        min: numeric, max: numeric, integerOnly: { bsonType: "bool" }
      }, ["integerOnly"]), { jumpRules: rules(numberCondition) }, reference)
    ]
  });
  const questionSchema = questionSchemaFor();
  const referencedQuestionSchema = questionSchemaFor({
    questionId: objectId, versionId: objectId, versionNo: positiveInt
  });
  // A library version contains content only; instance settings live on surveys.
  const contentSchema = {
    oneOf: questionSchema.oneOf.map((variant) => {
      const properties = { ...variant.properties };
      for (const name of ["id", "required", "jumpRules"]) delete properties[name];
      return object(properties);
    })
  };
  const responseStorage = { oneOf: [
    object({ mode: { enum: ["inline"] }, byteLength: nonNegativeInt(9007199254740991),
      sha256, data: { bsonType: "binData" } }),
    object({ mode: { enum: ["chunks"] }, byteLength: nonNegativeInt(9007199254740991),
      sha256, chunkCount: positiveInt })
  ] };
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
      schema: { ...object({
        _id: { bsonType: "objectId" }, ownerId: { bsonType: "objectId" },
        publicId: { bsonType: "string", pattern: "^[0-9a-f]{32}$" },
        title: text(200, 1), description: text(5000), status: { enum: ["draft", "published", "closed"] },
        settings: object({ anonymous: { bsonType: "bool" }, allowRepeat: { bsonType: "bool" } }),
        deadlineAt: dateOrNull, questions: { bsonType: "array", minItems: 0, maxItems: 100 },
        schemaVersion: { ...integer, enum: [1, 2] }, revision: { ...integer, minimum: 1 },
        createdAt: { bsonType: "date" }, updatedAt: { bsonType: "date" },
        publishedAt: dateOrNull, closedAt: dateOrNull, closeReason: { enum: [null, "manual", "deadline"] }
      }), oneOf: [
        { properties: { schemaVersion: { enum: [1] }, questions: array(questionSchema, 0, 100) } },
        { properties: { schemaVersion: { enum: [2] }, questions: array(referencedQuestionSchema, 0, 100) } }
      ] },
      indexes: [
        { key: { publicId: 1 }, name: "uq_surveys_publicId", unique: true },
        { key: { ownerId: 1, createdAt: -1, _id: -1 }, name: "ix_surveys_owner_created" },
        { key: { "questions.questionId": 1, ownerId: 1 }, name: "ix_surveys_question_owner" }
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
    },
    questions: {
      schema: object({
        _id: objectId, ownerId: objectId, latestVersionId: objectId,
        versionSeq: positiveInt, revision: positiveInt, guardRevision,
        origin: { oneOf: [
          object({ kind: { enum: ["original"] } }),
          object({ kind: { enum: ["copy"] }, questionId: objectId, versionId: objectId }),
          object({ kind: { enum: ["legacy"] }, surveyId: objectId, instanceId: id })
        ] },
        archivedAt: dateOrNull, createdAt: { bsonType: "date" }, updatedAt: { bsonType: "date" }
      }),
      indexes: [
        { key: { ownerId: 1, archivedAt: 1, updatedAt: -1, _id: -1 }, name: "ix_questions_owner_archived_updated" },
        { key: { "origin.surveyId": 1, "origin.instanceId": 1 }, name: "uq_questions_legacy_origin", unique: true,
          partialFilterExpression: { "origin.kind": "legacy" } }
      ]
    },
    question_versions: {
      schema: object({
        _id: objectId, questionId: objectId, versionNo: positiveInt,
        baseVersionId: { bsonType: ["objectId", "null"] },
        operation: { enum: ["create", "edit", "restore", "copy", "migrate"] },
        content: contentSchema, createdBy: objectId, createdAt: { bsonType: "date" }, archivedAt: dateOrNull
      }),
      indexes: [{ key: { questionId: 1, versionNo: 1 }, name: "uq_question_versions_question_number", unique: true }]
    },
    question_shares: {
      schema: object({
        _id: objectId, questionId: objectId, recipientId: objectId, grantedBy: objectId,
        grantedAt: { bsonType: "date" }, revokedAt: dateOrNull, revision: positiveInt,
        createdAt: { bsonType: "date" }, updatedAt: { bsonType: "date" }
      }),
      indexes: [
        { key: { questionId: 1, recipientId: 1 }, name: "uq_question_shares_question_recipient", unique: true },
        { key: { recipientId: 1, revokedAt: 1, questionId: 1 }, name: "ix_question_shares_recipient_active" }
      ]
    },
    question_banks: {
      schema: object({
        _id: objectId, ownerId: objectId, name: text(200, 1), description: text(2000),
        revision: positiveInt, guardRevision, createdAt: { bsonType: "date" }, updatedAt: { bsonType: "date" }
      }),
      indexes: [{ key: { ownerId: 1, updatedAt: -1, _id: -1 }, name: "ix_question_banks_owner_updated" }]
    },
    question_bank_items: {
      schema: object({ _id: objectId, bankId: objectId, questionId: objectId, createdAt: { bsonType: "date" } }),
      indexes: [
        { key: { bankId: 1, questionId: 1 }, name: "uq_question_bank_items_bank_question", unique: true },
        { key: { questionId: 1, bankId: 1 }, name: "ix_question_bank_items_question_bank" }
      ]
    },
    mutation_receipts: {
      schema: { ...object({
        _id: objectId, actorId: objectId,
        key: { bsonType: "string", pattern: "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$" },
        operationId: text(128, 1), target: text(1024), canonicalVersion: positiveInt, fingerprint: sha256,
        status: { enum: ["processing", "succeeded"] },
        resultRefs: { bsonType: "object", additionalProperties: { bsonType: ["objectId", "string"] } },
        httpStatus: { ...integer, minimum: 200, maximum: 299 }, response: responseStorage,
        createdAt: { bsonType: "date" }, completedAt: { bsonType: "date" }
      }, ["_id", "actorId", "key", "operationId", "target", "canonicalVersion", "fingerprint", "status", "resultRefs", "createdAt"]),
      oneOf: [
        { properties: { status: { enum: ["processing"] } }, not: { anyOf: [
          { required: ["httpStatus"] }, { required: ["response"] }, { required: ["completedAt"] }
        ] } },
        { properties: { status: { enum: ["succeeded"] } }, required: ["httpStatus", "response", "completedAt"] }
      ] },
      indexes: [{ key: { actorId: 1, key: 1 }, name: "uq_mutation_receipts_actor_key", unique: true }]
    },
    mutation_response_chunks: {
      schema: object({ _id: objectId, receiptId: objectId, index: nonNegativeInt(9007199254740991), data: { bsonType: "binData" } }),
      indexes: [{ key: { receiptId: 1, index: 1 }, name: "uq_mutation_response_chunks_receipt_index", unique: true }]
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
