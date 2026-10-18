import type { components, paths } from './schema';

/** Response and request bodies, named as in the FastAPI models. */
export type Schemas = components['schemas'];

/** Query-string parameters of a GET endpoint. */
export type QueryParams<P extends keyof paths> = paths[P] extends {
  get: { parameters: { query?: infer Q } };
}
  ? NonNullable<Q>
  : never;

export type AliasScope = NonNullable<Schemas['ReviewApproval']['alias_scope']>;
export type TaskKind = Schemas['Task']['kind'];
